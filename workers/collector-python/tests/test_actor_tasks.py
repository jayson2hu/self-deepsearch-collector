from __future__ import annotations

import json
import io
import base64
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import actor_tasks  # noqa: E402
from collector.connectors.jable import parse_jable_html  # noqa: E402
from collector.connectors.javdb_actors import parse_javdb_actor_list  # noqa: E402
from collector.jable_assets import ingest_jable_candidates  # noqa: E402
from collector.performer_assets import ingest_javdb_actor_candidates  # noqa: E402
from collector.storage import connect  # noqa: E402

OBSERVED_AT = "2026-09-18T00:00:00Z"
JABLE_ROOT = "https://jable.tv/models/"
JAVDB_ROOT = "https://javdb.com/actors"
ROBOTS = b"User-agent: *\nAllow: /\n"


class FakeFetcher:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.requests = []
        self.closed = False

    def fetch(self, url, limit, timeout):
        self.requests.append(url)
        if not 0 < timeout <= 20:
            raise AssertionError("request timeout escaped the runner bound")
        if url in self.responses:
            value = self.responses[url]
        elif url.endswith("/robots.txt"):
            value = ROBOTS
        else:
            raise AssertionError(f"unexpected simulated request: {url}")
        if isinstance(value, Exception):
            raise value
        if len(value) > limit:
            raise actor_tasks.FetchError("RESPONSE_TOO_LARGE")
        return value

    def close(self):
        self.closed = True


class ActorTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="actor-task-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed = self.root / "seed"
        self.workspace = self.root / "workspace"

    def seed_data(self, jable_count=1, javdb_count=0, avatar=False):
        self.seed.mkdir()
        database = self.seed / "collector.db"
        db = connect(database)
        db.close()
        if jable_count:
            cards = "".join(
                f'<a href="/models/actor-{number:03d}/"><h6>演员 {number}</h6>'
                f'<span>{number + 7} 部影片</span>'
                + ('<img src="https://assets-cdn.jable.tv/contents/models/1/alice.jpg">' if avatar and number == 0 else '')
                + '</a>' for number in range(jable_count)
            )
            candidates = parse_jable_html(cards, source_url=JABLE_ROOT, checked_at=OBSERVED_AT)
            ingest_jable_candidates(candidates, database, checked_at=OBSERVED_AT)
        if javdb_count:
            cards = "".join(f'<a href="/actors/AAA{number}" title="演员乙 {number}, 别名 {number}"></a>' for number in range(javdb_count))
            candidates = parse_javdb_actor_list(cards, source_url=JAVDB_ROOT, checked_at=OBSERVED_AT)
            ingest_javdb_actor_candidates(candidates, database, checked_at=OBSERVED_AT)
        self.refresh_seed_manifest()

    def refresh_seed_manifest(self):
        actor_tasks.write_json(self.seed / "files-manifest.json", {"files": [
            {"file": file.relative_to(self.seed).as_posix(), "bytes": file.stat().st_size,
             "sha256": actor_tasks.digest(file.read_bytes())}
            for file in sorted(self.seed.rglob("*")) if file.is_file() and file.name != "files-manifest.json"
        ]})

    def plan(self):
        return actor_tasks.plan(self.seed, self.workspace)

    def queue_rows(self, sql="SELECT * FROM tasks ORDER BY kind,source_id,url"):
        queue = actor_tasks.queue_connection(self.workspace)
        try:
            return [dict(row) for row in queue.execute(sql)]
        finally:
            queue.close()

    def execute_queue(self, sql, parameters=()):
        queue = actor_tasks.queue_connection(self.workspace)
        try:
            queue.execute(sql, parameters)
            queue.commit()
        finally:
            queue.close()

    def only_profiles(self):
        self.execute_queue("DELETE FROM tasks WHERE kind!='profile'")

    def run_tasks(self, fetcher, **limits):
        return actor_tasks.run(self.workspace, fetcher=fetcher, sleeper=lambda _: None, **limits)

    def test_plan_is_idempotent_and_includes_every_actor_beyond_sample_limits(self):
        self.seed_data(jable_count=73, javdb_count=2)
        first = self.plan()
        profiles = self.queue_rows("SELECT * FROM tasks WHERE kind='profile'")
        self.assertEqual(first["performers"], 75)
        self.assertEqual(len(profiles), 75)
        identifiers = {row["task_id"] for row in self.queue_rows()}
        self.assertEqual(len(identifiers), 80)  # All actors plus five observed source entrances.
        preserved = profiles[0]["task_id"]
        self.execute_queue("UPDATE tasks SET status='completed',attempts=2 WHERE task_id=?", (preserved,))
        self.plan()
        self.assertEqual({row["task_id"] for row in self.queue_rows()}, identifiers)
        row = next(row for row in self.queue_rows() if row["task_id"] == preserved)
        self.assertEqual((row["status"], row["attempts"]), ("completed", 2))
        exported = (self.workspace / "exports/performers.jsonl").read_text().splitlines()
        self.assertEqual(len(exported), 75)

    def test_seed_checksum_failure_does_not_create_partial_workspace(self):
        self.seed_data()
        with (self.seed / "collector.db").open("ab") as stream:
            stream.write(b"changed after manifest")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.plan()
        self.assertFalse(self.workspace.exists())

    def test_task_urls_reject_non_actor_pages_media_credentials_and_encoded_delimiters(self):
        rejected = [
            ("jable_reference", "profile", "https://jable.tv/models/2/"),
            ("jable_reference", "profile", "https://jable.tv/videos/example/"),
            ("jable_reference", "profile", "https://user:secret@jable.tv/models/alice/"),
            ("jable_reference", "profile", "https://jable.tv/models/alice/?token=hidden"),
            ("jable_reference", "profile", "https://jable.tv/models/alice;session=hidden/"),
            ("jable_reference", "profile", "https://jable.tv/models/alice%2Fvideos/"),
            ("jable_reference", "avatar", "https://assets-cdn.jable.tv/contents/videos/1/cover.jpg"),
            ("jable_reference", "avatar", "https://evil.example/contents/models/1/alice.jpg"),
            ("javdb_reference", "profile", "https://javdb.com/v/ABCD"),
            ("javdb_reference", "profile", "https://javdb.com/actors/censored"),
            ("javdb_reference", "avatar", "https://c0.jdbstatic.com/covers/aa/AAA0.jpg"),
            ("javdb_reference", "listing", "https://javdb.com/actors?page=2&page=3"),
            ("javdb_reference", "listing", "https://javdb.com/actors?page=%32"),
        ]
        for source, kind, url in rejected:
            with self.subTest(url=url):
                self.assertFalse(actor_tasks.allowed_url(source, kind, url))
        self.assertTrue(actor_tasks.allowed_url("jable_reference", "profile", "https://jable.tv/models/actor-000/"))
        self.assertTrue(actor_tasks.allowed_url("javdb_reference", "listing", "https://javdb.com/actors/censored?page=2"))

    def test_listing_pagination_enqueues_all_observed_pages_without_video_links(self):
        links = actor_tasks.listing_links("jable_reference", JABLE_ROOT,
            '<a href="/models/5/">Last</a><a href="/models/actor-000/">Actor</a><a href="/videos/1/">Video</a>')
        self.assertEqual(links, [f"https://jable.tv/models/{n}/" for n in range(2, 6)])

    def test_source_access_failure_blocks_that_source_and_other_source_continues(self):
        self.seed_data(jable_count=2, javdb_count=1)
        self.plan()
        fake = FakeFetcher({
            "https://jable.tv/robots.txt": actor_tasks.FetchError("HTTP_ERROR", 403),
            JAVDB_ROOT: b'<a href="/actors/AAA0" title="Actor Beta, Alias Beta"></a>',
        })
        result = self.run_tasks(fake, max_tasks=2)
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(fake.requests, ["https://jable.tv/robots.txt", "https://javdb.com/robots.txt", JAVDB_ROOT])
        states = {row["source_id"]: row for row in self.queue_rows("SELECT * FROM source_state")}
        self.assertEqual(states["jable_reference"]["blocked"], 1)
        self.assertEqual(states["javdb_reference"]["blocked"], 0)
        self.assertEqual(result["errors"][0]["http_status"], 403)
        self.assertTrue(fake.closed)
        self.assertFalse(any(row["source_id"] == "jable_reference" and row["status"] == "completed" for row in self.queue_rows()))

    def test_one_task_budget_restores_abandoned_running_task_and_preserves_the_rest(self):
        self.seed_data(jable_count=3)
        self.plan()
        self.only_profiles()
        first = self.queue_rows()[0]
        self.execute_queue("UPDATE tasks SET status='running',attempts=1 WHERE task_id=?", (first["task_id"],))
        fake = FakeFetcher({first["url"]: b'<main><h1>Actor Alpha</h1></main>'})
        result = self.run_tasks(fake, max_tasks=1)
        self.assertEqual((result["tasks_attempted"], result["tasks_completed"]), (1, 1))
        rows = self.queue_rows()
        self.assertEqual(sum(row["status"] == "completed" for row in rows), 1)
        self.assertEqual(sum(row["status"] == "pending" for row in rows), 2)
        self.assertEqual(next(row for row in rows if row["task_id"] == first["task_id"])["attempts"], 2)
        self.assertTrue(fake.closed)

    def test_time_budget_does_not_fetch_or_mark_a_task_complete(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        self.execute_queue("UPDATE source_state SET last_request_at=?", (time.time() + 90,))
        fake = FakeFetcher()
        result = self.run_tasks(fake, max_tasks=1, max_seconds=30)
        self.assertEqual(fake.requests, [])
        self.assertEqual(result["tasks_completed"], 0)
        self.assertEqual(self.queue_rows()[0]["status"], "pending")
        self.assertFalse(any(row["blocked"] for row in self.queue_rows("SELECT * FROM source_state")))

    def test_parse_failure_is_not_success_and_network_errors_have_no_fake_http_status(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        profile = self.queue_rows()[0]["url"]
        fake = FakeFetcher({profile: b'<html><body>Unrecognized page structure</body></html>'})
        result = self.run_tasks(fake, max_tasks=1)
        self.assertEqual(result["tasks_completed"], 0)
        self.assertEqual(self.queue_rows()[0]["status"], "blocked")
        self.assertIsNone(result["errors"][0]["http_status"])
        retry = FakeFetcher({"https://jable.tv/robots.txt": actor_tasks.FetchError("NETWORK_UNAVAILABLE")})
        failed = self.run_tasks(retry, max_tasks=1, retry_blocked=True)
        self.assertEqual(failed["tasks_completed"], 0)
        self.assertIsNone(failed["errors"][0]["http_status"])

    def test_export_keeps_missing_fields_unknown_and_each_fields_original_observation_time(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        profile = self.queue_rows()[0]["url"]
        fake = FakeFetcher({profile: b'<main><h1>Actor Alpha</h1><span data-performer-field="height">160 cm</span></main>'})
        result = self.run_tasks(fake, max_tasks=1)
        self.assertEqual(result["new_observations"], 1)
        row = json.loads((self.workspace / "exports/performers.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["height"], "160 cm")
        self.assertEqual(row["work_count"], 7)
        self.assertEqual(row["field_provenance"]["work_count"]["checked_at"], OBSERVED_AT)
        self.assertNotEqual(row["field_provenance"]["height"]["checked_at"], OBSERVED_AT)
        self.assertNotIn("birth_date", row)
        gap = json.loads((self.workspace / "exports/missing-fields.jsonl").read_text().splitlines()[0])
        self.assertIn("birth_date", gap["missing_fields"])
        self.assertIn("avatar", gap["missing_fields"])
        task = self.queue_rows()[0]
        evidence = json.loads((self.workspace / task["evidence"]).read_text())
        self.assertEqual(evidence["checked_at"], row["field_provenance"]["height"]["checked_at"])
        self.assertEqual(evidence["response_sha256"], actor_tasks.digest(fake.responses[profile]))

    def test_missing_downloaded_avatar_file_remains_a_reported_gap(self):
        self.seed_data(avatar=True)
        database = sqlite3.connect(self.seed / "collector.db")
        try:
            database.execute("UPDATE performer_media_candidates SET download_status='downloaded',local_path='media/missing.jpg',sha256=?", ("0" * 64,))
            database.commit()
        finally:
            database.close()
        self.refresh_seed_manifest()
        summary = self.plan()
        self.assertEqual(summary["avatars_downloaded"], 0)
        self.assertEqual(summary["missing_fields"]["avatar"], 1)

    def test_preexisting_failed_avatar_candidate_is_not_lost_from_the_full_plan(self):
        self.seed_data(avatar=True)
        with sqlite3.connect(self.seed / "collector.db") as database:
            database.execute("UPDATE performer_media_candidates SET download_status='failed'")
        database.close()
        self.refresh_seed_manifest()
        self.plan()
        avatars = self.queue_rows("SELECT * FROM tasks WHERE kind='avatar'")
        self.assertEqual(len(avatars), 1)
        self.assertEqual(avatars[0]["status"], "pending")

    def test_failed_ingest_recovers_saved_candidates_without_network_or_new_timestamp(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        profile = self.queue_rows()[0]["url"]
        fake = FakeFetcher({profile: b'<main><h1>Actor Alpha</h1><span data-performer-field="height">160 cm</span></main>'})
        with patch.object(actor_tasks, "ingest", side_effect=sqlite3.OperationalError("simulated storage failure")):
            failed = self.run_tasks(fake, max_tasks=1)
        self.assertEqual(failed["tasks_completed"], 0)
        before = self.queue_rows()[0]
        original = json.loads((self.workspace / before["evidence"]).read_text())
        self.assertEqual(before["status"], "blocked")
        self.assertEqual(before["evidence_sha256"], actor_tasks.digest((self.workspace / before["evidence"]).read_bytes()))

        offline = FakeFetcher()
        recovered = self.run_tasks(offline, max_tasks=1, retry_blocked=True)
        self.assertEqual(offline.requests, [])
        self.assertEqual((recovered["tasks_completed"], recovered["network_requests"], recovered["new_observations"]), (1, 0, 1))
        after = self.queue_rows()[0]
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["checked_at"], original["checked_at"])
        self.assertEqual(after["attempts"], 2)
        receipt = json.loads((self.workspace / after["evidence"]).read_text())
        self.assertTrue(receipt["recovered_without_network"])
        self.assertEqual(receipt["candidates"], original["candidates"])

    def test_committed_candidates_are_idempotent_when_failure_prevents_task_completion(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        profile = self.queue_rows()[0]["url"]
        fake = FakeFetcher({profile: b'<main><h1>Actor Alpha</h1></main>'})
        with patch.object(actor_tasks, "populate", side_effect=ValueError("simulated crash after data commit")):
            failed = self.run_tasks(fake, max_tasks=1)
        self.assertEqual((failed["tasks_completed"], failed["new_observations"]), (0, 1))
        offline = FakeFetcher()
        recovered = self.run_tasks(offline, max_tasks=1, retry_blocked=True)
        self.assertEqual((recovered["tasks_completed"], recovered["new_observations"]), (1, 0))
        self.assertEqual(offline.requests, [])
        with sqlite3.connect(self.workspace / "collector.db") as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM performers").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM performer_observations").fetchone()[0], 2)

    def test_tampered_saved_evidence_stops_without_silently_refetching(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        profile = self.queue_rows()[0]["url"]
        fake = FakeFetcher({profile: b'<main><h1>Actor Alpha</h1></main>'})
        with patch.object(actor_tasks, "ingest", side_effect=sqlite3.OperationalError("simulated storage failure")):
            self.run_tasks(fake, max_tasks=1)
        task = self.queue_rows()[0]
        with (self.workspace / task["evidence"]).open("a") as stream:
            stream.write(" ")
        offline = FakeFetcher()
        result = self.run_tasks(offline, max_tasks=1, retry_blocked=True)
        self.assertEqual(offline.requests, [])
        self.assertEqual(result["tasks_completed"], 0)
        self.assertEqual(result["errors"][0]["error_code"], "SAVED_EVIDENCE_INVALID")
        self.assertEqual(self.queue_rows()[0]["status"], "blocked")

    def test_robots_disallow_stops_before_requesting_actor_data(self):
        self.seed_data()
        self.plan()
        self.only_profiles()
        fake = FakeFetcher({"https://jable.tv/robots.txt": b"User-agent: *\nDisallow: /models/\n"})
        result = self.run_tasks(fake, max_tasks=1)
        self.assertEqual(fake.requests, ["https://jable.tv/robots.txt"])
        self.assertEqual(result["tasks_completed"], 0)
        self.assertEqual(result["errors"][0]["error_code"], "ROBOTS_DISALLOW")

    def test_browser_transport_kills_a_child_that_does_not_return_within_the_bound(self):
        process = Mock()
        process.stdin = io.StringIO()
        process.stdout = io.StringIO()
        process.poll.return_value = None
        process.pid = 123456
        mailbox = Mock()
        mailbox.get.side_effect = actor_tasks.thread_queue.Empty()
        with patch.object(actor_tasks.subprocess, "Popen", return_value=process), \
                patch.object(actor_tasks.threading, "Thread"), \
                patch.object(actor_tasks.thread_queue, "Queue", return_value=mailbox), \
                patch.object(actor_tasks.os, "killpg", create=True) as kill:
            fetcher = actor_tasks.BrowserFetcher()
            with self.assertRaises(actor_tasks.FetchError) as error:
                fetcher.fetch(JABLE_ROOT, 1024, 3)
            self.assertEqual(error.exception.code, "BROWSER_TIMEOUT")
            mailbox.get.assert_called_once_with(timeout=8)
            self.assertIsNone(fetcher.process)
            process.wait.assert_called_once()
            if actor_tasks.os.name != "nt":
                kill.assert_called_once_with(process.pid, actor_tasks.signal.SIGKILL)
            else:
                process.kill.assert_called_once()

    def test_observed_avatar_download_is_local_verified_and_requeued_if_the_file_is_lost(self):
        self.seed_data(avatar=True)
        self.plan()
        self.execute_queue("DELETE FROM tasks WHERE kind!='avatar'")
        task = self.queue_rows()[0]
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNdcAAAAASUVORK5CYII=")
        fake = FakeFetcher({task["url"]: png})
        completed = self.run_tasks(fake, max_tasks=1)
        self.assertEqual(completed["tasks_completed"], 1)
        summary = actor_tasks.report(self.workspace)
        self.assertEqual(summary["avatars_downloaded"], 1)
        with sqlite3.connect(self.workspace / "collector.db") as database:
            local_path, recorded_hash = database.execute("SELECT local_path,sha256 FROM performer_media_candidates").fetchone()
        self.assertEqual((self.workspace / local_path).read_bytes(), png)
        self.assertEqual(recorded_hash, actor_tasks.digest(png))
        (self.workspace / local_path).unlink()
        summary = self.plan()
        self.assertEqual(summary["avatars_downloaded"], 0)
        avatar = next(row for row in self.queue_rows() if row["kind"] == "avatar")
        self.assertEqual(avatar["status"], "pending")


if __name__ == "__main__":
    unittest.main()
