"""Versioned performer metadata export and resumable, source-bound HTTP delivery."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from collector.contracts import ContractError

SCHEMA = "collector.performers.v1"
MANIFEST_SCHEMA = "collector.performers.manifest.v1"
ENDPOINT_PATH = "/internal/v1/collector/performer-batches"
MAX_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
BATCH_FILE_PATTERN = re.compile(r"batch-[0-9]{3,8}\.json\Z")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")


def _hash(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON field")
        result[key] = value
    return result


def _decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)


def _text(value: Any, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(unicodedata.category(c) == "Cc" for c in value):
        raise ContractError(f"invalid {field}")
    return value


def _uuid(value: Any) -> str:
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise ContractError("source/batch ID must be a canonical lowercase UUID")
    return value


def _https_host(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ContractError("invalid source/profile URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or "?" in value or "#" in value:
        raise ContractError("source/profile URL must use HTTPS without credentials, query or fragment")
    parsed.port  # Validate port syntax before retaining the exact registered host/port.
    return parsed.netloc.lower()


def _checked_at(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ContractError("checked_at must be RFC3339 with timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.year < 2000 or parsed > datetime.now(UTC) + timedelta(minutes=5):
        raise ContractError("checked_at is outside the accepted interval")
    return value


def _validate_record(record: dict[str, Any], source_host: str) -> None:
    if not isinstance(record, dict) or set(record) != {"external_id", "checked_at", "payload"}:
        raise ContractError("invalid performer record fields")
    _text(record["external_id"], 255, "external_id")
    _checked_at(record["checked_at"])
    payload = record["payload"]
    if not isinstance(payload, dict) or set(payload) != {"name", "aliases", "profile_url", "source_content_hash"}:
        raise ContractError("invalid performer payload fields")
    _text(payload["name"], 200, "name")
    aliases = payload["aliases"]
    if not isinstance(aliases, list) or len(aliases) > 30:
        raise ContractError("invalid aliases")
    for alias in aliases:
        _text(alias, 200, "alias")
    if len(set(aliases)) != len(aliases):
        raise ContractError("duplicate aliases")
    if _https_host(payload["profile_url"]) != source_host:
        raise ContractError("profile URL host does not match the source mapping")
    if not isinstance(payload["source_content_hash"], str) or not HASH_PATTERN.fullmatch(payload["source_content_hash"]):
        raise ContractError("invalid source_content_hash")


def export_batches(database: Path, output_dir: Path, *, source_key: str, source_id: str, source_url: str,
                   region: str = "japan", batch_size: int = 500, max_records: int = 100000) -> dict[str, Any]:
    """Read one consistent SQLite snapshot; never infer rights or adult status."""
    _text(source_key, 200, "source_key")
    _uuid(source_id)
    source_host = _https_host(source_url)
    if region not in {"japan", "beijing"} or not 1 <= batch_size <= 500 or not 1 <= max_records <= 100000:
        raise ContractError("invalid region, batch size or maximum record count")
    if not database.is_file():
        raise ContractError("SQLite database does not exist")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = connection.execute("""
            SELECT p.external_id, p.name, p.profile_url, p.current_content_hash, p.checked_at,
                   p.rights_status, p.publication_status, o.candidate_json
            FROM performers p LEFT JOIN performer_observations o
              ON o.source_id=p.source_id AND o.external_id=p.external_id AND o.content_hash=p.current_content_hash
            WHERE p.source_id=? ORDER BY p.external_id LIMIT ?
        """, (source_key, max_records + 1)).fetchall()
    finally:
        connection.close()
    if not rows or len(rows) > max_records:
        raise ContractError("source mapping has no performers or exceeds --max-records")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["rights_status"] not in {"needs_review", "allowed"} or row["publication_status"] != "staging":
            raise ContractError("local performer rights/publication state is not eligible for candidate delivery")
        if not row["candidate_json"]:
            raise ContractError("current performer observation is missing")
        observation = _decode(row["candidate_json"].encode("utf-8"))
        if not isinstance(observation, dict) or not isinstance(observation.get("provenance"), dict) or not isinstance(observation.get("payload"), dict):
            raise ContractError("invalid stored performer observation")
        connector = _text(observation.get("provenance", {}).get("connector_version"), 200, "connector_version")
        record = {"external_id": row["external_id"], "checked_at": row["checked_at"], "payload": {
            "name": row["name"], "aliases": observation.get("payload", {}).get("aliases", []),
            "profile_url": row["profile_url"], "source_content_hash": row["current_content_hash"],
        }}
        _validate_record(record, source_host)
        groups.setdefault(connector, []).append(record)
    prepared: list[tuple[dict[str, Any], bytes]] = []

    def prepare(connector: str, records: list[dict[str, Any]]) -> None:
        body = {"schema_version": SCHEMA, "source_id": source_id, "region": region,
                "connector_version": connector, "records": records}
        body["idempotency_key"] = "sqlite-" + hashlib.sha256(_json_bytes(body)).hexdigest()
        raw = _json_bytes(body)
        if len(raw) > MAX_BYTES:
            if len(records) == 1:
                raise ContractError("one performer exceeds the request byte limit")
            middle = len(records) // 2
            prepare(connector, records[:middle])
            prepare(connector, records[middle:])
            return
        prepared.append((body, raw))

    for connector, records in sorted(groups.items()):
        for start in range(0, len(records), batch_size):
            prepare(connector, records[start:start + batch_size])
    output_dir.mkdir(parents=True, exist_ok=False)
    batches = []
    for index, (body, raw) in enumerate(prepared, start=1):
        name = f"batch-{index:03d}.json"
        (output_dir / name).write_bytes(raw)
        batches.append({"file": name, "records": len(body["records"]), "bytes": len(raw),
                        "request_hash": _hash(raw), "idempotency_key": body["idempotency_key"]})
    manifest = {"schema_version": MANIFEST_SCHEMA, "mode": "prepared-only", "source_key": source_key,
                "source_id": source_id, "source_url": source_url, "region": region, "records": len(rows),
                "rights_status": "needs_review", "publication_status": "staging", "adult_status": "unknown",
                "media_submitted": 0, "batches": batches}
    (output_dir / "manifest.json").write_bytes(_json_bytes(manifest) + b"\n")
    return manifest


def validate_manifest(path: Path) -> tuple[dict[str, Any], str, list[tuple[dict[str, Any], bytes]]]:
    raw_manifest = path.read_bytes()
    manifest = _decode(raw_manifest)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ContractError("unsupported manifest version")
    _uuid(manifest.get("source_id"))
    host = _https_host(manifest.get("source_url"))
    if manifest.get("region") not in {"japan", "beijing"}:
        raise ContractError("invalid manifest region")
    entries = manifest.get("batches")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100000:
        raise ContractError("manifest has no batches or is too large")
    verified = []
    names: set[str] = set()
    keys: set[str] = set()
    external_ids: set[str] = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"file", "records", "bytes", "request_hash", "idempotency_key"}:
            raise ContractError("invalid manifest batch fields")
        name = entry["file"]
        if not isinstance(name, str) or not BATCH_FILE_PATTERN.fullmatch(name) or name in names:
            raise ContractError("invalid or duplicate batch filename")
        names.add(name)
        batch_path = path.parent / name
        if batch_path.is_symlink() or not batch_path.is_file() or batch_path.stat().st_size > MAX_BYTES:
            raise ContractError("batch file is missing, linked or exceeds the byte limit")
        raw = batch_path.read_bytes()
        if len(raw) != entry["bytes"] or _hash(raw) != entry["request_hash"]:
            raise ContractError("batch integrity check failed")
        batch = _decode(raw)
        if not isinstance(batch, dict) or set(batch) != {"schema_version", "source_id", "region", "connector_version", "idempotency_key", "records"}:
            raise ContractError("invalid batch fields")
        if batch["schema_version"] != SCHEMA or batch["source_id"] != manifest["source_id"] or batch["region"] != manifest["region"]:
            raise ContractError("batch does not match the source mapping")
        _text(batch["connector_version"], 200, "connector_version")
        key = batch["idempotency_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", key) or key in keys or key != entry["idempotency_key"]:
            raise ContractError("invalid or duplicate idempotency key")
        keys.add(key)
        records = batch["records"]
        if not isinstance(records, list) or not 1 <= len(records) <= 500 or len(records) != entry["records"]:
            raise ContractError("invalid batch record count")
        for record in records:
            _validate_record(record, host)
            if record["external_id"] in external_ids:
                raise ContractError("duplicate performer in manifest")
            external_ids.add(record["external_id"])
        total += len(records)
        verified.append((entry, raw))
    if total != manifest.get("records") or total > 100000:
        raise ContractError("manifest total does not match batch records")
    return manifest, _hash(raw_manifest), verified


def _endpoint(value: str, allow_loopback_http: bool) -> str:
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ContractError("invalid endpoint")
    parsed = urllib.parse.urlsplit(value)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or "?" in value or "#" in value or parsed.path != ENDPOINT_PATH:
        raise ContractError("endpoint must name the collector v1 path without credentials, query or fragment")
    parsed.port
    if parsed.scheme != "https":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if parsed.scheme != "http" or not allow_loopback_http or not loopback:
            raise ContractError("endpoint requires HTTPS or explicit --allow-loopback-http with a literal loopback address")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _validate_receipt(receipt: Any, entry: dict[str, Any]) -> None:
    if not isinstance(receipt, dict) or receipt.get("status") != "staged" or receipt.get("request_hash") != entry["request_hash"]:
        raise ContractError("server receipt does not match the request")
    _uuid(receipt.get("batch_id"))
    _checked_at(receipt.get("received_at"))
    counts = [receipt.get(key) for key in ("input_count", "inserted_count", "duplicate_count")]
    if any(type(count) is not int or count < 0 for count in counts) or counts[0] != entry["records"] or counts[1] + counts[2] != counts[0]:
        raise ContractError("server receipt counts do not match the request")


def _durable_json(path: Path, value: Any) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _delivery_lock(directory: Path):
    """OS-managed lock is released after a crash, permitting the next run to resume."""
    path = directory / ".delivery.lock"
    if path.is_symlink():
        raise ContractError("delivery lock must not be a symlink")
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if not stream.tell():
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ContractError("another delivery is using this receipts directory") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def deliver_batches(manifest_path: Path, endpoint: str, receipts_dir: Path, *, allow_loopback_http: bool = False,
                    replay: bool = False, attempts: int = 3, timeout: float = 30,
                    retry_delay: float = 1) -> dict[str, Any]:
    endpoint = _endpoint(endpoint, allow_loopback_http)
    if not 1 <= attempts <= 10 or not 0 < timeout <= 120 or not 0 <= retry_delay <= 30:
        raise ContractError("invalid retry/timeout limits")
    token = os.environ.get("COLLECTOR_INGEST_TOKEN", "")
    if not 32 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ContractError("COLLECTOR_INGEST_TOKEN must contain 32-4096 printable ASCII characters without whitespace")
    manifest, manifest_hash, batches = validate_manifest(manifest_path)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    with _delivery_lock(receipts_dir):
        binding = {"endpoint": endpoint, "manifest_hash": manifest_hash, "source_id": manifest["source_id"]}
        # Verify every existing receipt before any network activity.
        stored: dict[str, dict[str, Any]] = {}
        for entry, _ in batches:
            path = receipts_dir / entry["file"]
            if path.exists():
                if path.is_symlink():
                    raise ContractError("receipt file must not be a symlink")
                previous = _decode(path.read_bytes())
                if not isinstance(previous, dict) or any(previous.get(k) != v for k, v in binding.items()):
                    raise ContractError("receipt belongs to a different endpoint or manifest; use another receipts directory")
                _validate_receipt(previous.get("receipt"), entry)
                stored[entry["file"]] = previous
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        summary = {"status": "staged", "source_id": manifest["source_id"], "manifest_hash": manifest_hash,
                   "batches": len(batches), "records": manifest["records"], "requests": 0, "submitted": 0, "skipped": 0, "replayed": 0}
        for entry, raw in batches:
            if entry["file"] in stored and not replay:
                summary["skipped"] += 1
                continue
            for attempt in range(attempts):
                request = urllib.request.Request(endpoint, data=raw, method="POST", headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json",
                    "X-Content-SHA256": entry["request_hash"],
                })
                summary["requests"] += 1
                try:
                    with opener.open(request, timeout=timeout) as response:
                        status = response.status
                        response_raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if status not in {200, 201} or len(response_raw) > MAX_RESPONSE_BYTES:
                        raise ContractError("unexpected server status or oversized receipt")
                    receipt = _decode(response_raw)
                    _validate_receipt(receipt, entry)
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    exc.close()
                    if status not in {429, 500, 502, 503, 504} or attempt + 1 == attempts:
                        raise ContractError(f"delivery stopped at {entry['file']}: HTTP {status}; saved receipts remain resumable") from None
                    time.sleep(min(retry_delay * (2 ** attempt), 30))
                    continue
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
                    if attempt + 1 == attempts:
                        raise ContractError(f"delivery stopped at {entry['file']}: transport failed; saved receipts remain resumable") from None
                    time.sleep(min(retry_delay * (2 ** attempt), 30))
                    continue
                _durable_json(receipts_dir / entry["file"], {**binding, "http_status": status, "receipt": receipt})
                summary["submitted"] += 1
                if status == 200:
                    summary["replayed"] += 1
                break
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-performer-batches")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--source-key", required=True)
    export.add_argument("--source-id", required=True)
    export.add_argument("--source-url", required=True)
    export.add_argument("--region", choices=["japan", "beijing"], default="japan")
    export.add_argument("--batch-size", type=int, default=500)
    export.add_argument("--max-records", type=int, default=100000)
    deliver = commands.add_parser("deliver-performer-batches")
    deliver.add_argument("--manifest", type=Path, required=True)
    deliver.add_argument("--endpoint", required=True)
    deliver.add_argument("--receipts-dir", type=Path, required=True)
    deliver.add_argument("--allow-loopback-http", action="store_true")
    deliver.add_argument("--replay", action="store_true")
    deliver.add_argument("--attempts", type=int, default=3)
    deliver.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == "export-performer-batches":
            result = export_batches(args.database, args.output_dir, source_key=args.source_key, source_id=args.source_id,
                                    source_url=args.source_url, region=args.region, batch_size=args.batch_size, max_records=args.max_records)
        else:
            result = deliver_batches(args.manifest, args.endpoint, args.receipts_dir, allow_loopback_http=args.allow_loopback_http,
                                     replay=args.replay, attempts=args.attempts, timeout=args.timeout)
    except (ContractError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        # Never expose untrusted server responses, credentials or SQL values in CLI output.
        message = str(exc) if isinstance(exc, ContractError) else "invalid input or local storage failure"
        print(json.dumps({"level": "error", "code": "COLLECTOR_DELIVERY_ERROR", "message": message}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
