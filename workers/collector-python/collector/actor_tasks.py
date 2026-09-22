"""Durable, bounded collection of public actor metadata and observed portraits."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import queue as thread_queue
import time
import urllib.error
import urllib.request
import urllib.robotparser
import uuid
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from collector.storage import connect, now_iso
from collector.media import NoRedirect, inspect_image, MAX_IMAGE_BYTES

ROOT = Path(__file__).resolve().parents[3]
AGENT = "self-deepsearch-collector"
SOURCES = {"jable_reference": ("jable.tv", 10), "javdb_reference": ("javdb.com", 20)}
TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL,
 url TEXT NOT NULL, priority INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, error_code TEXT, checked_at TEXT, evidence TEXT, evidence_sha256 TEXT,
 UNIQUE(source_id,kind,url)
);
CREATE TABLE IF NOT EXISTS source_state (
 source_id TEXT PRIMARY KEY, blocked INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, last_request_at REAL NOT NULL DEFAULT 0
);
"""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def allowed_url(source: str, kind: str, url: str) -> bool:
    if source not in SOURCES or not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        if any(value in url for value in ("\\", ";", "%")) or any(character.isspace() for character in url):
            return False
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
            return False
        if kind == "avatar":
            host, pattern = (("assets-cdn.jable.tv", r"/contents/models/[^?#]+\.(?:jpg|jpeg|png|webp)")
                             if source == "jable_reference" else ("c0.jdbstatic.com", r"/avatars/[A-Za-z0-9]{2}/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)"))
            return parsed.hostname == host and not parsed.query and bool(re.fullmatch(pattern, parsed.path, re.I))
        if parsed.hostname != SOURCES[source][0]:
            return False
        if kind == "robots":
            return parsed.path == "/robots.txt" and not parsed.query
        if source == "jable_reference":
            if parsed.query:
                return False
            if kind == "listing":
                match = re.fullmatch(r"/models/(?:(\d+)/)?", parsed.path)
                return bool(match and (not match[1] or 1 <= int(match[1]) <= 10000))
            return kind == "profile" and bool(re.fullmatch(r"/models/[^/]+/", parsed.path)) and not parsed.path.split("/")[2].isdecimal()
        if kind == "listing":
            query = parse_qs(parsed.query, keep_blank_values=True)
            return bool(re.fullmatch(r"/actors(?:/(?:censored|uncensored|western))?/?", parsed.path)) and (
                not parsed.query or (set(query) == {"page"} and len(query["page"]) == 1
                                     and bool(re.fullmatch(r"[1-9]\d{0,3}", query["page"][0])) and int(query["page"][0]) <= 1000))
        return kind == "profile" and not parsed.query and bool(re.fullmatch(r"/actors/[A-Za-z0-9_-]+", parsed.path)) and parsed.path.split("/")[-1] not in {"censored", "uncensored", "western"}
    except (ValueError, TypeError):
        return False


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            value = dict(attrs).get("href")
            if value:
                self.links.add(value)


def listing_links(source: str, url: str, text: str) -> list[str]:
    parser = Links()
    parser.feed(text)
    found = {urljoin(url, value) for value in parser.links}
    result = {value for value in found if allowed_url(source, "listing", value)}
    if source == "jable_reference":
        # The last numbered link is observed pagination evidence, not a guessed actor ID.
        last = max([int(urlsplit(value).path.split("/")[2]) for value in result if urlsplit(value).path != "/models/"] or [1])
        result.update(f"https://jable.tv/models/{number}/" for number in range(2, last + 1))
    return sorted(result)


def queue_connection(workdir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(workdir / "tasks.db")
    db.row_factory = sqlite3.Row
    db.executescript(TASK_SCHEMA)
    if 'evidence_sha256' not in {row[1] for row in db.execute('PRAGMA table_info(tasks)')}:
        db.execute('ALTER TABLE tasks ADD COLUMN evidence_sha256 TEXT')
    return db


@contextmanager
def runner_lock(workdir: Path):
    with (workdir / ".actor-runner.lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.write(b"0"); stream.flush(); stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another actor task runner is using this directory") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def enqueue(queue, source, kind, url, priority):
    if not allowed_url(source, kind, url):
        raise ValueError("Task URL is outside the actor collection scope")
    task_id = digest(f"{source}\0{kind}\0{url}".encode())
    queue.execute("INSERT OR IGNORE INTO tasks(task_id,source_id,kind,url,priority) VALUES(?,?,?,?,?)",
                  (task_id, source, kind, url, priority))
    queue.execute("INSERT OR IGNORE INTO source_state(source_id) VALUES(?)", (source,))


def bootstrap(seed: Path, workdir: Path):
    if workdir.exists():
        if not (workdir / "actor-workspace.json").is_file():
            raise ValueError("Existing output is not an actor workspace")
        return
    database = seed / "collector.db"
    manifest_path = next((seed / name for name in ("files-manifest.json", "archive-manifest.json") if (seed / name).is_file()), None)
    if manifest_path is None:
        raise ValueError("Seed must have a verified file manifest")
    files = {item["file"]: item for item in json.loads(manifest_path.read_text())["files"]}
    if database.with_name(database.name + "-wal").exists():
        raise ValueError("Seed must be an offline snapshot without an active WAL")
    for name, item in files.items():
        file = (seed / name).resolve()
        if not file.is_relative_to(seed.resolve()) or not file.is_file() or file.stat().st_size != item["bytes"] or digest(file.read_bytes()) != item["sha256"]:
            raise ValueError("Seed checksum verification failed")
    if "collector.db" not in files:
        raise ValueError("Seed database is not covered by its manifest")
    workdir.mkdir(parents=True)
    try:
        source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        target = sqlite3.connect(workdir / "collector.db")
        try:
            source.backup(target)
        finally:
            target.close(); source.close()
        for name in files:
            if name.startswith("media/"):
                target = workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(seed / name, target)
        write_json(workdir / "actor-workspace.json", {"schema_version": 1, "created_at": now_iso(),
            "seed_directory": str(seed.resolve()), "seed_manifest_sha256": digest(manifest_path.read_bytes()),
            "scope": "public_actor_metadata_and_observed_portraits", "publication_status": "staging"})
    except BaseException:
        shutil.rmtree(workdir)
        raise


def verified_avatar(media, root):
    try:
        if media["download_status"] != "downloaded" or not media["local_path"]:
            return False
        path = (root / media["local_path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            return False
        raw = path.read_bytes()
        mime, _, width, height = inspect_image(raw)
        return digest(raw) == media["sha256"] and len(raw) == media["byte_size"] and (mime, width, height) == (media["mime_type"], media["width"], media["height"])
    except (OSError, ValueError, TypeError):
        return False


def populate(queue, database):
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        for p in db.execute("SELECT p.*, EXISTS(SELECT 1 FROM performer_media_candidates m WHERE m.performer_id=p.performer_id AND m.purpose='avatar' AND m.download_status='downloaded') has_avatar FROM performers p"):
            if p["source_id"] in SOURCES:
                enqueue(queue, p["source_id"], "profile", p["profile_url"], 30 if p["has_avatar"] else 20)
        for m in db.execute("SELECT * FROM performer_media_candidates WHERE purpose='avatar' AND download_status IN ('pending','downloaded','failed') AND rights_status IN ('needs_review','allowed') AND review_status!='rejected'"):
            if verified_avatar(m, database.parent):
                continue
            if m["source_id"] in SOURCES and allowed_url(m["source_id"], "avatar", m["candidate_url"]):
                enqueue(queue, m["source_id"], "avatar", m["candidate_url"], 15)
                queue.execute("UPDATE tasks SET status='pending' WHERE source_id=? AND kind='avatar' AND url=? AND status='completed'", (m["source_id"], m["candidate_url"]))
    finally:
        db.close()
    queue.commit()


def plan(seed: Path, workdir: Path, config_path: Path = ROOT / "data/collection/actor-tasks.json") -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != "actor-collection-plan@1" or not config.get("sources"):
        raise ValueError("Invalid actor task configuration")
    for entry in config["sources"]:
        source = entry["source_id"]
        if source not in SOURCES or type(entry["minimum_interval_seconds"]) is not int or not SOURCES[source][1] <= entry["minimum_interval_seconds"] <= 300:
            raise ValueError("Invalid source interval")
        if not entry["listing_seeds"] or not all(allowed_url(source, "listing", url) for url in entry["listing_seeds"]):
            raise ValueError("Invalid listing seed")
    bootstrap(seed, workdir)
    with runner_lock(workdir):
        write_json(workdir / "task-config.json", config)
        queue = queue_connection(workdir)
        try:
            for entry in config["sources"]:
                for url in entry["listing_seeds"]:
                    enqueue(queue, entry["source_id"], "listing", url, 10)
            populate(queue, workdir / "collector.db")
        finally:
            queue.close()
    return report(workdir)


class FetchError(Exception):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code, self.status = code, status


class HttpFetcher:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())

    def fetch(self, url, limit, timeout):
        try:
            with self.opener.open(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=timeout) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise FetchError("RESPONSE_TOO_LARGE")
                if response.status != 200:
                    raise FetchError("HTTP_ERROR", response.status)
                return raw
        except urllib.error.HTTPError as exc:
            raise FetchError("HTTP_ERROR", exc.code) from None
        except (OSError, urllib.error.URLError):
            raise FetchError("NETWORK_UNAVAILABLE") from None

    def close(self):
        pass


class BrowserFetcher(HttpFetcher):
    def __init__(self):
        super().__init__()
        self.process = None

    def fetch(self, url, limit, timeout):
        if urlsplit(url).hostname not in {"jable.tv", "javdb.com"}:
            return super().fetch(url, limit, timeout)
        if self.process is None:
            self.process = subprocess.Popen(["node", str(ROOT / "scripts" / "actor_browser.mjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                start_new_session=os.name != "nt")
        try:
            self.process.stdin.write(json.dumps({"url": url, "limit": limit, "timeout": timeout}) + "\n")
            self.process.stdin.flush()
            mailbox = thread_queue.Queue()
            threading.Thread(target=lambda: mailbox.put(self.process.stdout.readline()), daemon=True).start()
            result = json.loads(mailbox.get(timeout=timeout + 5))
        except thread_queue.Empty:
            self.stop()
            raise FetchError("BROWSER_TIMEOUT") from None
        except (OSError, ValueError):
            raise FetchError("BROWSER_UNAVAILABLE") from None
        if result.get("error"):
            raise FetchError(result["error"], result.get("http_status"))
        import base64
        raw = base64.b64decode(result["body"], validate=True)
        if len(raw) > limit:
            raise FetchError("RESPONSE_TOO_LARGE")
        return raw

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
            self.process = None

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.stop()


def parse_page(source, kind, raw, url, checked_at):
    text = raw.decode("utf-8")
    if source == "jable_reference":
        from collector.connectors.jable import parse_jable_html
        return parse_jable_html(text, source_url=url, checked_at=checked_at)
    from collector.connectors.javdb_actors import parse_javdb_actors
    return parse_javdb_actors(text, source_url=url, checked_at=checked_at)


def ingest(source, candidates, database, checked_at):
    if source == "jable_reference":
        from collector.jable_assets import ingest_jable_candidates
        return ingest_jable_candidates(candidates, database, checked_at=checked_at)
    from collector.performer_assets import ingest_javdb_actor_candidates
    return ingest_javdb_actor_candidates(candidates, database, checked_at=checked_at)


def save_avatar(database, workdir, source, url, raw):
    mime, suffix, width, height = inspect_image(raw)
    db = connect(database)
    try:
        rows = db.execute("SELECT * FROM performer_media_candidates WHERE source_id=? AND candidate_url=? AND purpose='avatar' AND rights_status IN ('needs_review','allowed') AND review_status!='rejected'", (source, url)).fetchall()
        if not rows:
            raise ValueError("Avatar has no eligible observed candidate")
        for row in rows:
            relative = f"media/{row['media_candidate_id']}{suffix}"
            path = workdir / relative
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".part")
            temporary.write_bytes(raw); temporary.replace(path)
            db.execute("UPDATE performer_media_candidates SET download_status='downloaded',local_path=?,sha256=?,mime_type=?,width=?,height=?,byte_size=?,updated_at=? WHERE media_candidate_id=?",
                       (relative, digest(raw), mime, width, height, len(raw), now_iso(), row["media_candidate_id"]))
        db.commit()
    finally:
        db.close()


def run(workdir: Path, *, max_tasks=50, max_seconds=600, retry_blocked=False, transport="http", fetcher=None, sleeper=time.sleep) -> dict:
    if not (workdir / "actor-workspace.json").is_file():
        raise ValueError("Run plan-actor-tasks first")
    if not 1 <= max_tasks <= 10000 or not 30 <= max_seconds <= 43200:
        raise ValueError("max-tasks must be 1..10000; max-seconds must be 30..43200")
    fetcher = fetcher or (BrowserFetcher() if transport == "browser" else HttpFetcher())
    started = time.monotonic()
    run_dir = workdir / "runs" / (now_iso().replace(":", "").replace(".", "-") + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True)
    result = {"started_at": now_iso(), "transport": transport, "tasks_attempted": 0, "tasks_completed": 0, "new_observations": 0, "errors": [], "network_requests": 0}
    configuration = json.loads((workdir / "task-config.json").read_text()) if (workdir / "task-config.json").is_file() else {"sources": []}
    intervals = {entry["source_id"]: entry["minimum_interval_seconds"] for entry in configuration["sources"]}
    try:
        with runner_lock(workdir):
            queue = queue_connection(workdir)
            try:
                # OS lock proves no other runner owns abandoned running tasks.
                queue.execute("UPDATE tasks SET status='pending' WHERE status='running'")
                if retry_blocked:
                    queue.execute("UPDATE source_state SET blocked=0,error_code=NULL")
                    queue.execute("UPDATE tasks SET status='pending',error_code=NULL WHERE status='blocked'")
                queue.commit()
                robots = {}

                def request(source, url, limit):
                    last = queue.execute("SELECT last_request_at FROM source_state WHERE source_id=?", (source,)).fetchone()[0]
                    delay = max(SOURCES[source][1], intervals.get(source, 0), robots.get(source, (None, 0))[1] or 0)
                    pause = max(0, last + delay - time.time())
                    remaining = max_seconds - (time.monotonic() - started)
                    if pause + 2 >= remaining:
                        raise FetchError("TIME_BUDGET")
                    sleeper(pause)
                    queue.execute("UPDATE source_state SET last_request_at=? WHERE source_id=?", (time.time(), source)); queue.commit()
                    result["network_requests"] += 1
                    return fetcher.fetch(url, limit, min(20, max(1, remaining - pause)))

                def store_evidence(task, evidence):
                    source = task["source_id"]
                    with sqlite3.connect(workdir / "collector.db") as db:
                        before = db.execute("SELECT COUNT(*) FROM performer_observations").fetchone()[0]
                    stored = ingest(source, evidence["candidates"], workdir / "collector.db", evidence["checked_at"])
                    with sqlite3.connect(workdir / "collector.db") as db:
                        after = db.execute("SELECT COUNT(*) FROM performer_observations").fetchone()[0]
                    result["new_observations"] += after - before
                    evidence["ingest"] = stored
                    for url in evidence.get("discovered_listing_urls", []):
                        enqueue(queue, source, "listing", url, 10)
                    populate(queue, workdir / "collector.db")

                def complete(task, evidence):
                    evidence["status"] = "completed"
                    path = run_dir / f"{task['task_id']}.json"
                    write_json(path, evidence)
                    queue.execute("UPDATE tasks SET status='completed',error_code=NULL,checked_at=?,evidence=?,evidence_sha256=? WHERE task_id=?",
                                  (evidence["checked_at"], path.relative_to(workdir).as_posix(), digest(path.read_bytes()), task["task_id"]))
                    queue.commit()
                    result["tasks_completed"] += 1

                for _ in range(max_tasks):
                    if time.monotonic() - started >= max_seconds:
                        break
                    row = queue.execute("SELECT t.* FROM tasks t JOIN source_state s USING(source_id) WHERE t.status='pending' AND s.blocked=0 ORDER BY t.priority,t.source_id,CASE WHEN t.kind='listing' THEN length(t.url) ELSE 0 END,t.url LIMIT 1").fetchone()
                    if not row:
                        break
                    task = dict(row); source = task["source_id"]
                    queue.execute("UPDATE tasks SET status='running',attempts=attempts+1 WHERE task_id=?", (task["task_id"],)); queue.commit()
                    result["tasks_attempted"] += 1
                    checked_at = now_iso()
                    evidence = {"task_id": task["task_id"], "source_id": source, "kind": task["kind"], "source_url": task["url"], "checked_at": checked_at}
                    try:
                        if not allowed_url(source, task["kind"], task["url"]):
                            raise FetchError("INVALID_TASK_URL")
                        if task["evidence"] and task["kind"] != "avatar":
                            saved_path = (workdir / task["evidence"]).resolve()
                            if not saved_path.is_relative_to(workdir.resolve()) or not saved_path.is_file() or digest(saved_path.read_bytes()) != task["evidence_sha256"]:
                                raise FetchError("SAVED_EVIDENCE_INVALID")
                            saved = json.loads(saved_path.read_text(encoding="utf-8"))
                            if saved.get("task_id") != task["task_id"] or saved.get("source_url") != task["url"] or not isinstance(saved.get("candidates"), list):
                                raise FetchError("SAVED_EVIDENCE_INVALID")
                            store_evidence(task, saved)
                            saved["recovered_without_network"] = True
                            complete(task, saved)
                            continue
                        if source not in robots:
                            body = request(source, f"https://{SOURCES[source][0]}/robots.txt", 65536)
                            decoded = body.decode("utf-8")
                            if "<html" in decoded.lower() or not re.search(r"(?im)^\s*user-agent\s*:", decoded):
                                raise FetchError("INVALID_ROBOTS")
                            parser = urllib.robotparser.RobotFileParser(); parser.parse(decoded.splitlines())
                            robots[source] = (parser, parser.crawl_delay(AGENT) or 0)
                            (run_dir / f"{source}-robots.txt").write_bytes(body)
                        check_url = task["url"] if task["kind"] != "avatar" else f"https://{SOURCES[source][0]}/{'models/' if source == 'jable_reference' else 'actors'}"
                        if not robots[source][0].can_fetch(AGENT, check_url):
                            raise FetchError("ROBOTS_DISALLOW")
                        raw = request(source, task["url"], MAX_IMAGE_BYTES if task["kind"] == "avatar" else 2 * 1024 * 1024)
                        checked_at = now_iso(); evidence.update(checked_at=checked_at, bytes=len(raw), response_sha256=digest(raw))
                        if task["kind"] == "avatar":
                            save_avatar(workdir / "collector.db", workdir, source, task["url"], raw)
                        else:
                            candidates = parse_page(source, task["kind"], raw, task["url"], checked_at)
                            evidence["candidates"] = candidates
                            evidence["discovered_listing_urls"] = listing_links(source, task["url"], raw.decode("utf-8")) if task["kind"] == "listing" else []
                            # The evidence survives a crash between saving the page and ingestion.
                            captured_path = run_dir / f"{task['task_id']}-captured.json"
                            write_json(captured_path, evidence)
                            queue.execute("UPDATE tasks SET evidence=?,evidence_sha256=? WHERE task_id=?", (captured_path.relative_to(workdir).as_posix(), digest(captured_path.read_bytes()), task["task_id"]))
                            queue.commit()
                            store_evidence(task, evidence)
                        complete(task, evidence)
                    except (FetchError, ValueError, UnicodeError, sqlite3.Error) as exc:
                        code = exc.code if isinstance(exc, FetchError) else "PARSE_OR_INGEST_ERROR"
                        evidence.update(status="blocked", error_code=code, http_status=getattr(exc, "status", None))
                        write_json(run_dir / f"{task['task_id']}.json", evidence)
                        result["errors"].append({"source_id": source, "task_id": task["task_id"], "error_code": code, "http_status": getattr(exc, "status", None)})
                        if code == "TIME_BUDGET":
                            queue.execute("UPDATE tasks SET status='pending' WHERE task_id=?", (task["task_id"],)); queue.commit(); break
                        queue.execute("UPDATE tasks SET status='blocked',error_code=? WHERE task_id=?", (code, task["task_id"]))
                        queue.execute("UPDATE source_state SET blocked=1,error_code=? WHERE source_id=?", (code, source))
                    queue.commit()
                    write_json(run_dir / "run-report.json", result)
            finally:
                queue.close()
    finally:
        fetcher.close()
    result.update(completed_at=now_iso(), status="stopped_on_error" if result["errors"] else "batch_complete")
    write_json(run_dir / "run-report.json", result)
    summary = report(workdir)
    if not result["errors"]:
        result["status"] = "completed" if summary["all_tasks_completed"] else ("blocked" if any(s["blocked"] for s in summary["sources"]) else "batch_complete")
        write_json(run_dir / "run-report.json", result)
    return {**result, "queue": summary["tasks"], "report": str(run_dir / "run-report.json")}


def report(workdir: Path) -> dict:
    from collector.jable_assets import load_current_performer_payloads
    database = workdir / "collector.db"
    db = sqlite3.connect(database); db.row_factory = sqlite3.Row
    queue = queue_connection(workdir)
    try:
        summaries = [dict(row) for row in queue.execute("SELECT source_id,kind,status,COUNT(*) count FROM tasks GROUP BY source_id,kind,status ORDER BY source_id,kind,status")]
        fields = {source: load_current_performer_payloads(db, source_id=source) for source in SOURCES}
        profiles = []
        gaps = []
        for row in db.execute("SELECT * FROM performers ORDER BY source_id,external_id"):
            p = dict(row)
            merged = fields.get(p["source_id"], {}).get(p["external_id"], {})
            p.update(merged.get("payload", {})); p["field_provenance"] = merged.get("field_provenance", {})
            avatars = [dict(item) for item in db.execute("SELECT media_candidate_id,local_path,sha256,download_status,byte_size,mime_type,width,height FROM performer_media_candidates WHERE performer_id=? AND purpose='avatar' ORDER BY checked_at DESC,media_candidate_id", (p["performer_id"],))]
            for avatar in avatars:
                avatar["verified_locally"] = verified_avatar(avatar, workdir)
            p["avatars"] = avatars
            missing = [field for field in ("name", "profile_url", "aliases", "biography", "birth_date", "height") if not p.get(field)]
            if not any(m["verified_locally"] for m in avatars):
                missing.append("avatar")
            p["missing_fields"] = missing
            profiles.append(p)
            if missing:
                gaps.append({"source_id": p["source_id"], "external_id": p["external_id"], "profile_url": p["profile_url"], "missing_fields": missing, "reason": "not_observed_or_not_downloaded"})
        source_states = [dict(row) for row in queue.execute("SELECT source_id,blocked,error_code FROM source_state ORDER BY source_id")]
    finally:
        db.close(); queue.close()
    destination = workdir / "exports"; destination.mkdir(exist_ok=True)
    for name, rows in (("performers.jsonl", profiles), ("missing-fields.jsonl", gaps)):
        (destination / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    with (destination / "performers.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        names = ["source_id", "external_id", "name", "profile_url", "work_count", "aliases", "biography", "birth_date", "height", "checked_at"]
        writer = csv.DictWriter(stream, fieldnames=names, extrasaction="ignore"); writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in p.items()} for p in profiles)
    result = {"checked_at": now_iso(), "performers": len(profiles), "avatars_downloaded": sum(any(m["verified_locally"] for m in p["avatars"]) for p in profiles),
        "tasks": summaries, "sources": source_states, "missing_fields": {field: sum(field in p["missing_fields"] for p in profiles) for field in ("name", "profile_url", "avatar", "aliases", "biography", "birth_date", "height")},
        "all_tasks_completed": not any(row["status"] != "completed" for row in summaries), "full_site_coverage": False,
        "notes": ["Missing fields remain unknown until an explicit source observation supplies them.", "Names do not automatically link actors across sources.", "A completed listing scan does not prove full-site historical coverage."],
        "exports": "exports/", "database": "collector.db", "task_database": "tasks.db"}
    write_json(workdir / "task-report.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan-actor-tasks")
    planning.add_argument("--seed-dir", type=Path, required=True)
    planning.add_argument("--output-dir", type=Path, required=True)
    planning.add_argument("--config", type=Path, default=ROOT / "data/collection/actor-tasks.json")
    running = sub.add_parser("run-actor-tasks")
    running.add_argument("--work-dir", type=Path, required=True)
    running.add_argument("--max-tasks", type=int, default=50)
    running.add_argument("--max-seconds", type=int, default=600)
    running.add_argument("--retry-blocked", action="store_true")
    running.add_argument("--transport", choices=("http", "browser"), default="http")
    reporting = sub.add_parser("report-actor-tasks")
    reporting.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan-actor-tasks":
            result = plan(args.seed_dir, args.output_dir, args.config)
        elif args.command == "run-actor-tasks":
            result = run(args.work_dir, max_tasks=args.max_tasks, max_seconds=args.max_seconds, retry_blocked=args.retry_blocked, transport=args.transport)
        else:
            result = report(args.work_dir)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("status") in {"stopped_on_error", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
