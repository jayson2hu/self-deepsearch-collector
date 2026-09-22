from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.contracts import ContractError
from collector.delivery import ENDPOINT_PATH, MAX_BYTES, _delivery_lock, _hash, _json_bytes, deliver_batches, export_batches, validate_manifest
from collector.storage import connect

SOURCE_ID = "12345678-1234-1234-1234-123456789abc"
TOKEN = "synthetic-test-token-never-real-123456789"


def make_database(path: Path, count: int = 3) -> None:
    connection = connect(path)
    connection.execute("INSERT INTO collection_runs(run_id,source_id,connector_version,checked_at,started_at,status) VALUES('run','example','fixture@v1','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','completed')")
    for index in range(count):
        external = f"person-{index:04d}"
        payload = {"name": f"人物 {index}", "aliases": [f"别名 {index}"], "profile_url": f"https://example.test/models/{external}/"}
        candidate = {"payload": payload, "provenance": {"connector_version": "fixture@v1"}}
        connection.execute("INSERT INTO performers VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), "example", external, payload["name"], payload["profile_url"], "sha256:" + "a" * 64,
            "needs_review", "staging", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ))
        connection.execute("INSERT INTO performer_observations VALUES(?,?,?,?,?,?,?)", (
            "example", external, "sha256:" + "a" * 64, "run", json.dumps(candidate), "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        ))
    connection.commit()
    connection.close()


@contextmanager
def fake_server(actions: list[int | str] | None = None):
    requests = []
    receipts = {}
    pending = list(actions or [])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
            action = pending.pop(0) if pending else 201
            if isinstance(action, int) and action not in {200, 201}:
                self.send_response(action)
                if 300 <= action < 400:
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirect-target")
                self.end_headers()
                self.wfile.write(b"must-not-be-logged")
                return
            payload = json.loads(body)
            request_hash = _hash(body)
            existing = request_hash in receipts
            receipt = receipts.setdefault(request_hash, {"batch_id": str(uuid.uuid4()), "request_hash": request_hash,
                "status": "staged", "input_count": len(payload["records"]), "inserted_count": len(payload["records"]),
                "duplicate_count": 0, "received_at": datetime.now(UTC).isoformat()})
            if action == "lost":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if action == "wrong_receipt":
                receipt = {**receipt, "request_hash": "sha256:" + "f" * 64}
            if action == "wrong_count":
                receipt = {**receipt, "duplicate_count": 100}
            raw = _json_bytes(receipt)
            self.send_response(200 if existing else 201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{ENDPOINT_PATH}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "collector.db"
        make_database(self.database)
        self.env = patch.dict(os.environ, {"COLLECTOR_INGEST_TOKEN": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)

    def prepare(self, name="prepared", **kwargs):
        directory = self.root / name
        export_batches(self.database, directory, source_key="example", source_id=SOURCE_ID,
                       source_url="https://example.test", batch_size=2, **kwargs)
        return directory / "manifest.json"

    def send(self, manifest, endpoint, **kwargs):
        return deliver_batches(manifest, endpoint, self.root / "receipts", allow_loopback_http=True, retry_delay=0, **kwargs)

    def test_export_is_deterministic_preserves_aliases_and_leaves_database_unchanged(self):
        before = self.database.read_bytes()
        first, second = self.prepare(), self.prepare("again")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        manifest = json.loads(first.read_bytes())
        self.assertEqual(manifest["records"], 3)
        self.assertEqual(manifest["adult_status"], "unknown")
        self.assertEqual(manifest["media_submitted"], 0)
        for entry in manifest["batches"]:
            raw = (first.parent / entry["file"]).read_bytes()
            self.assertEqual(raw, (second.parent / entry["file"]).read_bytes())
            self.assertEqual(_hash(raw), entry["request_hash"])
        body = json.loads((first.parent / "batch-001.json").read_bytes())
        self.assertEqual(body["records"][0]["payload"]["aliases"], ["别名 0"])
        self.assertNotIn("adult_status", body["records"][0]["payload"])
        self.assertEqual(before, self.database.read_bytes())
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_rejects_missing_source_host_mismatch_and_missing_current_observation(self):
        with self.assertRaisesRegex(ContractError, "no performers"):
            export_batches(self.database, self.root / "missing", source_key="other", source_id=SOURCE_ID, source_url="https://example.test")
        with self.assertRaisesRegex(ContractError, "host"):
            export_batches(self.database, self.root / "wrong", source_key="example", source_id=SOURCE_ID, source_url="https://wrong.test")
        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM performer_observations")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ContractError, "observation is missing"):
            self.prepare()

    def test_known_restrictions_and_non_staging_states_are_not_downgraded(self):
        for rights, publication in (("restricted", "staging"), ("takedown", "staging"), ("allowed", "published")):
            with self.subTest(rights=rights, publication=publication):
                connection = sqlite3.connect(self.database)
                connection.execute("UPDATE performers SET rights_status=?,publication_status=?", (rights, publication))
                connection.commit()
                connection.close()
                with self.assertRaisesRegex(ContractError, "rights/publication"):
                    self.prepare()
                self.assertFalse((self.root / "prepared").exists())
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE performers SET rights_status='allowed',publication_status='staging'")
        connection.commit()
        connection.close()
        manifest = json.loads(self.prepare().read_bytes())
        self.assertEqual(manifest["rights_status"], "needs_review")
        self.assertEqual(manifest["adult_status"], "unknown")

    def test_export_rejects_future_time_and_excessive_record_limit(self):
        with self.assertRaisesRegex(ContractError, "max-records"):
            self.prepare(max_records=2)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE performers SET checked_at='2999-01-01T00:00:00Z'")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(ContractError, "checked_at"):
            self.prepare()

    def test_http_delivery_resume_and_explicit_replay_use_identical_bytes(self):
        manifest = self.prepare()
        with fake_server() as (endpoint, requests):
            result = self.send(manifest, endpoint)
            self.assertEqual((result["requests"], result["submitted"], result["replayed"]), (2, 2, 0))
            resumed = self.send(manifest, endpoint)
            self.assertEqual((resumed["requests"], resumed["skipped"]), (0, 2))
            replayed = self.send(manifest, endpoint, replay=True)
            self.assertEqual((replayed["submitted"], replayed["replayed"]), (2, 2))
        self.assertEqual(len(requests), 4)
        self.assertEqual(requests[0]["body"], requests[2]["body"])
        self.assertEqual(requests[1]["body"], requests[3]["body"])
        self.assertEqual(requests[0]["headers"]["Authorization"], "Bearer " + TOKEN)
        for path in (self.root / "receipts").glob("*.json"):
            self.assertNotIn(TOKEN, path.read_text())

    def test_response_loss_retries_same_bytes_and_gets_original_receipt(self):
        manifest = self.prepare()
        with fake_server(["lost"]) as (endpoint, requests):
            result = self.send(manifest, endpoint)
        self.assertEqual(result["requests"], 3)
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(requests[0]["body"], requests[1]["body"])

    def test_partial_failure_keeps_completed_receipt_for_next_run(self):
        manifest = self.prepare()
        with fake_server([201, 503]) as (endpoint, requests):
            with self.assertRaisesRegex(ContractError, "HTTP 503"):
                self.send(manifest, endpoint, attempts=1)
            self.assertTrue((self.root / "receipts" / "batch-001.json").exists())
            self.assertFalse((self.root / "receipts" / "batch-002.json").exists())
            result = self.send(manifest, endpoint)
            self.assertEqual((result["skipped"], result["submitted"]), (1, 1))
        self.assertEqual(len(requests), 3)

    def test_invalid_credentials_conflicts_and_redirects_are_not_retried(self):
        manifest = self.prepare()
        for status in (400, 401, 403, 409, 302, 307):
            with self.subTest(status=status), fake_server([status]) as (endpoint, requests):
                with self.assertRaisesRegex(ContractError, f"HTTP {status}"):
                    self.send(manifest, endpoint)
                self.assertEqual(len(requests), 1)
                self.assertFalse(list((self.root / "receipts").glob("*.json")))

    def test_integrity_of_every_batch_is_checked_before_first_request(self):
        manifest = self.prepare()
        path = manifest.parent / "batch-002.json"
        path.write_bytes(path.read_bytes() + b" ")
        with fake_server() as (endpoint, requests):
            with self.assertRaisesRegex(ContractError, "integrity"):
                self.send(manifest, endpoint)
            self.assertEqual(requests, [])

    def test_rejects_path_traversal_and_source_mapping_tampering(self):
        path = self.prepare()
        manifest = json.loads(path.read_bytes())
        manifest["batches"][0]["file"] = "../outside.json"
        path.write_bytes(_json_bytes(manifest))
        with self.assertRaisesRegex(ContractError, "filename"):
            validate_manifest(path)
        path = self.prepare("again")
        manifest = json.loads(path.read_bytes())
        manifest["source_id"] = "22345678-1234-1234-1234-123456789abc"
        path.write_bytes(_json_bytes(manifest))
        with self.assertRaisesRegex(ContractError, "source mapping"):
            validate_manifest(path)

    def test_receipt_validation_prevents_bad_acknowledgements(self):
        manifest = self.prepare()
        for action in ("wrong_receipt", "wrong_count"):
            with self.subTest(action=action), fake_server([action]) as (endpoint, requests):
                with self.assertRaises(ContractError):
                    self.send(manifest, endpoint)
                self.assertEqual(len(requests), 1)
                self.assertFalse(list((self.root / "receipts").glob("*.json")))

    def test_receipts_are_bound_to_endpoint_and_manifest(self):
        manifest = self.prepare()
        with fake_server() as (endpoint, _):
            self.send(manifest, endpoint)
        with fake_server() as (other_endpoint, requests):
            with self.assertRaisesRegex(ContractError, "different endpoint or manifest"):
                self.send(manifest, other_endpoint)
            self.assertEqual(requests, [])

    def test_plaintext_remote_credentials_and_proxy_use_are_blocked(self):
        manifest = self.prepare()
        for endpoint in ("http://example.test" + ENDPOINT_PATH, "http://localhost" + ENDPOINT_PATH,
                         "https://user:secret@example.test" + ENDPOINT_PATH, "https://example.test" + ENDPOINT_PATH + "?secret=yes"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ContractError):
                self.send(manifest, endpoint)
        with fake_server() as (endpoint, requests), patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""}):
            result = self.send(manifest, endpoint)
            self.assertEqual(result["submitted"], 2)
            self.assertEqual(len(requests), 2)
        with self.assertRaisesRegex(ContractError, "HTTPS"):
            deliver_batches(manifest, "http://127.0.0.1" + ENDPOINT_PATH, self.root / "other")

    def test_concurrent_delivery_cannot_reuse_receipts_directory(self):
        manifest = self.prepare()
        directory = self.root / "receipts"
        directory.mkdir()
        with _delivery_lock(directory), fake_server() as (endpoint, requests):
            with self.assertRaisesRegex(ContractError, "another delivery"):
                self.send(manifest, endpoint)
            self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
