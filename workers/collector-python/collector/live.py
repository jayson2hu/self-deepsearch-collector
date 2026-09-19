from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from collector.connectors.javdb import CONNECTOR_VERSION, SOURCE_ID, extract_media_candidates, parse_detail, validate_detail_url
from collector.storage import complete_run, connect, database_stats, ingest_candidate, now_iso, record_error, start_run

MAX_URLS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _load_scope(path: Path, robots_file: Path) -> dict[str, Any]:
    scope = json.loads(path.read_text(encoding="utf-8"))
    if scope.get("source_id") != SOURCE_ID or scope.get("connector_version") != CONNECTOR_VERSION:
        raise ValueError("scope source_id or connector_version is not approved")
    if scope.get("network_access") is not True or scope.get("acknowledgement") != "public_metadata_review_only":
        raise ValueError("scope requires network_access=true and public_metadata_review_only acknowledgement")
    urls = scope.get("urls")
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS or len(set(urls)) != len(urls):
        raise ValueError(f"scope requires 1-{MAX_URLS} unique URLs")
    for url in urls:
        if not isinstance(url, str):
            raise ValueError("scope URLs must be strings")
        validate_detail_url(url)
    interval = scope.get("minimum_interval_seconds")
    if not isinstance(interval, (int, float)) or interval < 20:
        raise ValueError("minimum_interval_seconds must be at least 20")
    response_bytes = scope.get("response_bytes")
    if not isinstance(response_bytes, int) or not 1 <= response_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError(f"response_bytes must be between 1 and {MAX_RESPONSE_BYTES}")
    timeout = scope.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
        raise ValueError("timeout_seconds must be between 1 and 30")
    robots = robots_file.read_text(encoding="utf-8")
    if "Crawl-delay: 20" not in robots:
        raise ValueError("robots evidence does not contain the observed 20-second crawl delay")
    return scope


def collect_review_scope(scope_path: Path, robots_file: Path, database: Path, output_dir: Path) -> dict[str, Any]:
    scope = _load_scope(scope_path, robots_file)
    checked_at = now_iso()
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    connection = connect(database)
    run_id = start_run(connection, source_id=SOURCE_ID, connector_version=CONNECTOR_VERSION, checked_at=checked_at, discovered_count=len(scope["urls"]))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())
    fetched = 0
    parsed_count = 0
    items = []
    previous_request = 0.0
    try:
        for source_url in scope["urls"]:
            external_id, canonical_url = validate_detail_url(source_url)
            snapshot: dict[str, Any] | None = None
            remaining = float(scope["minimum_interval_seconds"]) - (time.monotonic() - previous_request)
            if previous_request and remaining > 0:
                time.sleep(remaining)
            previous_request = time.monotonic()
            request = urllib.request.Request(canonical_url, headers={"User-Agent": "self-deepsearch-collector/0.4 public-metadata-review"})
            try:
                with opener.open(request, timeout=float(scope["timeout_seconds"])) as response:
                    if response.status != 200:
                        raise ValueError(f"unexpected HTTP {response.status}")
                    content_type = response.headers.get_content_type()
                    if content_type != "text/html":
                        raise ValueError(f"unexpected content type {content_type}")
                    raw = response.read(int(scope["response_bytes"]) + 1)
                fetched += 1
                if len(raw) > int(scope["response_bytes"]):
                    raise ValueError("response exceeds the configured byte limit")
                destination = raw_dir / f"{external_id}.html"
                destination.write_bytes(raw)
                snapshot = {
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_file": str(destination),
                }
                text = raw.decode("utf-8")
                candidate = parse_detail(text, source_url=canonical_url, checked_at=checked_at)
                media = extract_media_candidates(text, source_url=canonical_url, checked_at=checked_at)
                ingest_candidate(connection, run_id, candidate, media)
                parsed_count += 1
                items.append({
                    "url": canonical_url,
                    "external_id": external_id,
                    "status": "stored",
                    **snapshot,
                    "media_candidates": len(media),
                })
            except urllib.error.HTTPError as exc:
                location = exc.headers.get("Location") if exc.headers else None
                message = f"HTTP {exc.code}" + (f" redirect={location}" if location else "")
                record_error(connection, run_id, source_url=canonical_url, error_code="HTTP_ERROR", message=message)
                items.append({"url": canonical_url, "external_id": external_id, "status": "error", "message": message})
            except (OSError, UnicodeDecodeError, urllib.error.URLError, ValueError) as exc:
                record_error(connection, run_id, source_url=canonical_url, error_code="FETCH_OR_PARSE_ERROR", message=str(exc))
                items.append({"url": canonical_url, "external_id": external_id, "status": "error", "message": str(exc), **(snapshot or {})})
        complete_run(connection, run_id, fetched_count=fetched, failed=parsed_count == 0)
        stats = database_stats(connection)
    finally:
        connection.close()
    report = {
        "run_id": run_id,
        "source_id": SOURCE_ID,
        "connector_version": CONNECTOR_VERSION,
        "checked_at": checked_at,
        "discovered": len(scope["urls"]),
        "fetched": fetched,
        "parsed": parsed_count,
        "stored": parsed_count,
        "omitted": len(scope["urls"]) - parsed_count,
        "complete_for_scope": parsed_count == len(scope["urls"]),
        "minimum_interval_seconds": scope["minimum_interval_seconds"],
        "database_stats": stats,
        "items": items,
    }
    (output_dir / "live-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a bounded manual review collection against explicit JavDB detail URLs")
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--robots-file", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = collect_review_scope(args.scope, args.robots_file, args.database, args.output_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "LIVE_COLLECTION_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
