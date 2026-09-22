from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from collector.connectors.jable import CONNECTOR_VERSION, SOURCE_ID, _media_url, parse_jable_html, performer_content_hash
from collector.media import MAX_IMAGE_BYTES, NoRedirect, inspect_image
from collector.storage import complete_run, connect, now_iso, performer_entity_id, start_run


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("source_id") != SOURCE_ID:
        raise ValueError(f"source_id must be {SOURCE_ID}")
    if manifest.get("connector_version") not in {CONNECTOR_VERSION, "jable-html@2026-09-19.1", "jable-html@2026-09-17.1"}:
        raise ValueError(f"connector_version must be {CONNECTOR_VERSION}")
    checked_at = manifest.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at:
        raise ValueError("checked_at is required")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 5:
        raise ValueError("samples must contain between 1 and 5 items")
    return manifest


def parse_jable_manifest(manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = _read_manifest(manifest_path)
    manifest_root = manifest_path.parent.resolve()
    observations: list[dict[str, Any]] = []
    sample_reports = []
    for sample in manifest["samples"]:
        if not isinstance(sample, dict) or not isinstance(sample.get("file"), str) or not isinstance(sample.get("url"), str):
            raise ValueError("each Jable sample requires file and url")
        sample_path = (manifest_root / sample["file"]).resolve()
        if manifest_root not in sample_path.parents or not sample_path.is_file():
            raise ValueError("Jable sample path escapes the manifest directory or is missing")
        raw = sample_path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Jable sample exceeds 2 MiB")
        text = raw.decode("utf-8")
        checked_at = sample.get("checked_at", manifest["checked_at"])
        if not isinstance(checked_at, str) or not checked_at:
            raise ValueError("sample checked_at must be a nonempty timestamp")
        candidates = parse_jable_html(text, source_url=sample["url"], checked_at=checked_at)
        observations.extend(candidates)
        sample_reports.append({
            "file": sample["file"], "url": sample["url"], "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "performers": len(candidates),
        })

    # Each line is one actual page observation. Combining page fields here would
    # falsely attribute older listing facts to a later profile capture.
    performers = sorted(observations, key=lambda item: str(item["payload"].get("name", item["external_id"])).casefold())
    media = list({item["media_candidate_id"]: item for performer in performers for item in performer["media"]}.values())
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "performers.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in performers), encoding="utf-8",
    )
    (output_dir / "media_candidates.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in media), encoding="utf-8",
    )
    report = {
        "source_id": SOURCE_ID,
        "connector_version": CONNECTOR_VERSION,
        "network_access": False,
        "sample_kind": manifest.get("sample_kind", "provided_html_snapshot"),
        "samples": sample_reports,
        "performers": len({item["external_id"] for item in performers}),
        "observations": len(performers),
        "avatar_candidates": sum(item["purpose"] == "avatar" for item in media),
        "gallery_candidates": sum(item["purpose"] == "gallery" for item in media),
    }
    _write_json(output_dir / "parse-report.json", report)
    return report


def ingest_jable_manifest(manifest_path: Path, database: Path, output_dir: Path) -> dict[str, Any]:
    report = parse_jable_manifest(manifest_path, output_dir)
    candidates = [json.loads(line) for line in (output_dir / "performers.jsonl").read_text(encoding="utf-8").splitlines() if line]
    manifest = _read_manifest(manifest_path)
    result = {**report, **ingest_jable_candidates(candidates, database, checked_at=manifest["checked_at"])}
    _write_json(output_dir / "ingest-report.json", result)
    return result


def load_current_performer_payloads(connection, *, source_id: str = SOURCE_ID) -> dict[str, dict[str, Any]]:
    """Resolve latest nonempty fields without synthesizing source observations.

    Provenance belongs to the observation that supplied each field, and therefore
    keeps its original timestamp when a newer profile omits a listing-only fact.
    """
    resolved: dict[str, dict[str, Any]] = {}
    rows = connection.execute(
        """SELECT external_id, content_hash, candidate_json, checked_at
           FROM performer_observations WHERE source_id=?
           ORDER BY julianday(checked_at), created_at, rowid""", (source_id,),
    )
    for row in rows:
        candidate = json.loads(row["candidate_json"])
        current = resolved.setdefault(row["external_id"], {"payload": {}, "field_provenance": {}})
        provenance = candidate.get("provenance", {})
        for key, value in candidate.get("payload", {}).items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            current["payload"][key] = value
            current["field_provenance"][key] = {
                "source_url": provenance.get("source_url"), "checked_at": row["checked_at"],
                "content_hash": row["content_hash"], "connector_version": provenance.get("connector_version"),
            }
    return resolved


def ingest_jable_candidates(candidates: list[dict[str, Any]], database: Path, *, checked_at: str) -> dict[str, Any]:
    """Persist already parsed candidates, retaining their original source facts."""
    for candidate in candidates:
        if candidate.get("source_id") != SOURCE_ID or performer_content_hash(candidate) != candidate.get("content_hash"):
            raise ValueError("Jable candidate source or content hash mismatch")
        observed_at = datetime.fromisoformat(candidate["provenance"]["checked_at"].replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("Jable candidate checked_at must include a timezone")
        if any(item.get("purpose") != "avatar" or _media_url(item["candidate_url"], item["source_page_url"]) != item["candidate_url"] for item in candidate["media"]):
            raise ValueError("Jable candidate contains an unsupported portrait URL or media purpose")
    connection = connect(database)
    run_id = None
    try:
        run_id = start_run(
            connection, source_id=SOURCE_ID, connector_version=CONNECTOR_VERSION,
            checked_at=checked_at, discovered_count=len(candidates),
        )
        for candidate in sorted(candidates, key=lambda item: not bool(item["payload"].get("name"))):
            timestamp = now_iso()
            external_id = str(candidate["external_id"])
            performer_id = performer_entity_id(SOURCE_ID, external_id)
            payload = candidate["payload"]
            checked_at = candidate["provenance"]["checked_at"]
            existing = connection.execute(
                "SELECT name FROM performers WHERE source_id=? AND external_id=?", (SOURCE_ID, external_id),
            ).fetchone()
            name = payload.get("name") or (existing["name"] if existing else None)
            if not name:
                raise ValueError(f"Jable performer {external_id} has no observed name and no existing record")
            connection.execute(
                """
                INSERT INTO performers (
                  performer_id, source_id, external_id, name, profile_url, current_content_hash,
                  checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, external_id) DO UPDATE SET
                  name=excluded.name, profile_url=excluded.profile_url,
                  current_content_hash=excluded.current_content_hash,
                  checked_at=excluded.checked_at, updated_at=excluded.updated_at
                WHERE julianday(excluded.checked_at) >= julianday(performers.checked_at)
                """,
                (performer_id, SOURCE_ID, external_id, name, payload["profile_url"],
                 candidate["content_hash"], checked_at, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO performer_observations (
                  source_id, external_id, content_hash, run_id, candidate_json, checked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (SOURCE_ID, external_id, candidate["content_hash"], run_id,
                 json.dumps(candidate, ensure_ascii=False, separators=(",", ":")), checked_at, timestamp),
            )
            for item in candidate["media"]:
                connection.execute(
                    """
                    INSERT INTO performer_media_candidates (
                      media_candidate_id, performer_id, source_id, external_id, candidate_url,
                      source_page_url, purpose, display_position, rights_status, download_status,
                      review_status, checked_at, connector_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_candidate_id) DO UPDATE SET
                      display_position=excluded.display_position, checked_at=excluded.checked_at,
                      source_page_url=excluded.source_page_url,
                      connector_version=excluded.connector_version, updated_at=excluded.updated_at
                    WHERE julianday(excluded.checked_at) >= julianday(performer_media_candidates.checked_at)
                    """,
                    (item["media_candidate_id"], performer_id, SOURCE_ID, external_id,
                     item["candidate_url"], item["source_page_url"], item["purpose"],
                     item["display_position"], item["rights_status"], item["download_status"],
                     item["review_status"], item["checked_at"], item["connector_version"], timestamp, timestamp),
                )
            connection.execute(
                """
                UPDATE collection_runs SET parsed_count=parsed_count+1, stored_count=stored_count+1,
                  media_candidate_count=media_candidate_count+? WHERE run_id=?
                """,
                (len(candidate["media"]), run_id),
            )
        connection.commit()
        complete_run(connection, run_id, fetched_count=len(candidates))
        counts = {
            "performers": int(connection.execute("SELECT COUNT(*) FROM performers WHERE source_id=?", (SOURCE_ID,)).fetchone()[0]),
            "observations": int(connection.execute("SELECT COUNT(*) FROM performer_observations WHERE source_id=?", (SOURCE_ID,)).fetchone()[0]),
            "media_candidates": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=?", (SOURCE_ID,)).fetchone()[0]),
        }
    except Exception:
        connection.rollback()
        if run_id is not None:
            connection.execute("UPDATE collection_runs SET status='failed', completed_at=?, error_count=error_count+1 WHERE run_id=?", (now_iso(), run_id))
            connection.commit()
        raise
    finally:
        connection.close()
    return {"run_id": run_id, "database": str(database), "database_counts": counts}


def download_jable_media(
    database: Path,
    output_dir: Path,
    *,
    max_images: int,
    acknowledgement: str,
    retry_transient: bool = False,
) -> dict[str, Any]:
    if acknowledgement != "internal_review_only":
        raise ValueError("acknowledgement must be internal_review_only")
    if not 1 <= max_images <= 50:
        raise ValueError("max_images must be between 1 and 50")
    output_dir.mkdir(parents=True, exist_ok=True)
    history = output_dir / "download-reports"
    history.mkdir(exist_ok=True)
    previous_report = output_dir / "download-report.json"
    if previous_report.is_file():
        previous_bytes = previous_report.read_bytes()
        archived = history / f"previous-{hashlib.sha256(previous_bytes).hexdigest()[:16]}.json"
        if not archived.exists():
            archived.write_bytes(previous_bytes)
    transient_ids: set[str] = set()
    if retry_transient:
        for saved in sorted(history.glob("*.json"), key=lambda item: item.stat().st_mtime_ns):
            previous = json.loads(saved.read_text(encoding="utf-8"))
            for item in previous.get("items_this_run", []):
                transient_ids.discard(item["media_candidate_id"])
            for item in previous.get("errors_this_run", []):
                transient_ids.discard(item["media_candidate_id"])
                if item.get("error_type") in {"URLError", "TimeoutError"} and item.get("http_status") is None:
                    transient_ids.add(item["media_candidate_id"])
    retry_clause = " OR (download_status='failed' AND media_candidate_id IN (" + ",".join("?" for _ in transient_ids) + "))" if transient_ids else ""
    connection = connect(database)
    rows = connection.execute(
        f"""
        SELECT media_candidate_id, candidate_url FROM performer_media_candidates
        WHERE source_id=? AND (download_status='pending'{retry_clause}) AND purpose='avatar'
          AND rights_status IN ('needs_review', 'allowed') AND review_status!='rejected'
        ORDER BY CASE purpose WHEN 'avatar' THEN 0 ELSE 1 END, external_id, display_position
        LIMIT ?
        """,
        (SOURCE_ID, *sorted(transient_ids), max_images),
    ).fetchall()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())
    downloaded = []
    errors = []
    attempted = 0
    halted_on_access = False
    try:
        for index, row in enumerate(rows):
            if index:
                time.sleep(2)
            attempted += 1
            url = row["candidate_url"]
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            try:
                if _media_url(url, url) != url or host != "assets-cdn.jable.tv" or not parsed.path.startswith("/contents/models/"):
                    raise ValueError("Jable download must be an observed model portrait on the approved CDN")
                if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
                    raise ValueError("Jable media URL contains forbidden components")
                request = urllib.request.Request(url, headers={"User-Agent": "self-deepsearch-collector/0.4 internal-review"})
                with opener.open(request, timeout=20) as response:
                    if response.status != 200:
                        raise ValueError(f"unexpected HTTP {response.status}")
                    raw = response.read(MAX_IMAGE_BYTES + 1)
                if len(raw) > MAX_IMAGE_BYTES:
                    raise ValueError("image exceeds 10 MiB")
                mime_type, suffix, width, height = inspect_image(raw)
                digest = hashlib.sha256(raw).hexdigest()
                destination = output_dir / f"{row['media_candidate_id']}{suffix}"
                temporary = destination.with_suffix(destination.suffix + ".part")
                temporary.write_bytes(raw)
                temporary.replace(destination)
                connection.execute(
                    """
                    UPDATE performer_media_candidates SET download_status='downloaded', local_path=?,
                      sha256=?, mime_type=?, width=?, height=?, byte_size=?, updated_at=?
                    WHERE media_candidate_id=?
                    """,
                    (str(destination.resolve()), digest, mime_type, width, height, len(raw), now_iso(), row["media_candidate_id"]),
                )
                connection.commit()
                downloaded.append({"media_candidate_id": row["media_candidate_id"], "path": str(destination), "sha256": digest, "mime_type": mime_type, "width": width, "height": height, "byte_size": len(raw)})
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
                connection.execute(
                    "UPDATE performer_media_candidates SET download_status='failed', updated_at=? WHERE media_candidate_id=?",
                    (now_iso(), row["media_candidate_id"]),
                )
                connection.commit()
                errors.append({"media_candidate_id": row["media_candidate_id"], "error_type": type(exc).__name__, "http_status": getattr(exc, "code", None)})
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {403, 429}:
                    halted_on_access = True
                    break
        totals = {
            "downloaded": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=? AND download_status='downloaded'", (SOURCE_ID,)).fetchone()[0]),
            "failed": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=? AND download_status='failed'", (SOURCE_ID,)).fetchone()[0]),
        }
    finally:
        connection.close()
    report = {
        "checked_at": now_iso(), "retry_transient": retry_transient,
        "requested_this_run": len(rows), "downloaded_this_run": len(downloaded),
        "attempted_this_run": attempted, "unattempted_this_run": len(rows) - attempted, "halted_on_access": halted_on_access,
        "failed_this_run": len(errors), "database_totals": totals,
        "staging_only": True, "rights_status": "needs_review",
        "items_this_run": downloaded, "errors_this_run": errors,
    }
    _write_json(output_dir / "download-report.json", report)
    _write_json(history / f"{report['checked_at'].replace(':', '')}.json", report)
    return report


def build_performer_showcase(database: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / "media"
    media_dir.mkdir(exist_ok=True)
    connection = connect(database)
    try:
        performers = [dict(row) for row in connection.execute(
            """
            SELECT p.performer_id, p.external_id, p.name, p.profile_url, p.rights_status,
              p.publication_status, p.checked_at, o.candidate_json
            FROM performers p LEFT JOIN performer_observations o
              ON o.source_id=p.source_id AND o.external_id=p.external_id AND o.content_hash=p.current_content_hash
            WHERE p.source_id=? ORDER BY p.name
            """,
            (SOURCE_ID,),
        )]
        current_payloads = load_current_performer_payloads(connection)
        media_by_performer: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            """
            SELECT performer_id, media_candidate_id, purpose, display_position, local_path, sha256,
              mime_type, width, height, byte_size, rights_status, review_status
            FROM performer_media_candidates
            WHERE source_id=? AND download_status='downloaded' AND purpose='avatar'
              AND rights_status IN ('needs_review', 'allowed') AND review_status!='rejected'
            ORDER BY performer_id, CASE purpose WHEN 'avatar' THEN 0 ELSE 1 END, display_position
            """,
            (SOURCE_ID,),
        ):
            media_by_performer.setdefault(row["performer_id"], []).append(dict(row))
    finally:
        connection.close()
    for performer in performers:
        observation = json.loads(performer.pop("candidate_json") or "{}")
        current = current_payloads.get(performer["external_id"], {"payload": {}, "field_provenance": {}})
        performer.update(current["payload"])
        performer.setdefault("work_count", None)
        performer["field_provenance"] = current["field_provenance"]
        performer["source_page_url"] = observation.get("provenance", {}).get("source_url")
        performer["avatar"] = None
        performer["gallery"] = []
        for item in media_by_performer.get(performer["performer_id"], []):
            source = Path(item["local_path"])
            if not source.is_absolute():
                source = database.parent / source
            target = media_dir / f"{item['media_candidate_id']}{source.suffix.lower()}"
            shutil.copyfile(source, target)
            rendered = {**item, "url": f"media/{target.name}"}
            if item["purpose"] == "avatar" and performer["avatar"] is None:
                performer["avatar"] = rendered
            else:
                performer["gallery"].append(rendered)
    performers.sort(key=lambda item: (item["avatar"] is None, item["name"].casefold()))
    dataset = {
        "source_id": SOURCE_ID, "real_data": True, "publication_mode": "internal_acceptance_only",
        "counts": {
            "performers": len(performers),
            "avatars": sum(item["avatar"] is not None for item in performers),
            "gallery_images": sum(len(item["gallery"]) for item in performers),
            "without_downloaded_avatar": sum(item["avatar"] is None for item in performers),
        },
        "performers": performers,
    }
    coverage_path = output_dir / "coverage-report.json"
    if coverage_path.is_file():
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        if coverage.get("source_id") == SOURCE_ID:
            dataset["coverage"] = {key: coverage[key] for key in ("captured_page_count", "discovered_last_page", "listing_pages_complete", "duplicate_occurrences", "checked_at")}
    _write_json(output_dir / "performers.json", dataset)
    template = Path(__file__).resolve().parents[3] / "scripts" / "templates" / "jable_performers.html"
    page = template.read_text(encoding="utf-8").replace("__DATASET__", json.dumps(dataset, ensure_ascii=False).replace("<", "\\u003c"))
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    report = {"output_dir": str(output_dir), **dataset["counts"], "external_requests": 0}
    _write_json(output_dir / "showcase-report.json", report)
    return report


def ingest_jable_crawl(report_path: Path, database: Path) -> dict[str, Any]:
    crawl = json.loads(report_path.read_text(encoding="utf-8"))
    if crawl.get("source_id") != SOURCE_ID or not isinstance(crawl.get("pages"), list):
        raise ValueError("Invalid Jable crawl report")
    root = report_path.parent.resolve()
    pages = []
    for page in crawl["pages"]:
        if page.get("status") != "captured":
            continue
        directory = Path(page["directory"]).resolve()
        if root not in directory.parents:
            raise ValueError("Captured page escapes crawl directory")
        raw = (directory / "models.html").read_bytes()
        if hashlib.sha256(raw).hexdigest() != page["projection_sha256"]:
            raise ValueError("Captured page hash mismatch")
        result = ingest_jable_manifest(directory / "manifest.json", database, directory / "ingested")
        pages.append({"page": page["page"], "performers": result["performers"], "run_id": result["run_id"]})
    result = {"source_id": SOURCE_ID, "pages_ingested": pages, "visited_pages": crawl["visited_pages"],
              "pending_pages": crawl["pending_pages"], "discovered_last_page": crawl["discovered_last_page"],
              "full_site_coverage": False, "database": str(database)}
    _write_json(root / "crawl-ingest-report.json", result)
    return result


def audit_jable(database: Path, output_dir: Path) -> dict[str, Any]:
    import sqlite3
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        performers = [dict(row) for row in connection.execute("SELECT * FROM performers WHERE source_id=?", (SOURCE_ID,))]
        media = [dict(row) for row in connection.execute("SELECT * FROM performer_media_candidates WHERE source_id=? AND purpose='avatar'", (SOURCE_ID,))]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    failures = []
    verified = []
    for item in media:
        if item["download_status"] != "downloaded":
            continue
        try:
            media_path = Path(item["local_path"])
            raw = (media_path if media_path.is_absolute() else database.parent / media_path).read_bytes()
            mime, _, width, height = inspect_image(raw)
            valid = hashlib.sha256(raw).hexdigest() == item["sha256"] and len(raw) == item["byte_size"] and (mime, width, height) == (item["mime_type"], item["width"], item["height"])
            if not valid:
                raise ValueError("file_metadata_mismatch")
            verified.append(item["media_candidate_id"])
        except (OSError, ValueError):
            failures.append(item["media_candidate_id"])
    gaps = []
    for performer in performers:
        candidates = [item for item in media if item["performer_id"] == performer["performer_id"]]
        if any(item["media_candidate_id"] in verified for item in candidates):
            continue
        gaps.append({"external_id": performer["external_id"], "name": performer["name"], "profile_url": performer["profile_url"],
                     "reason": "no_portrait_in_captured_list" if not candidates else "portrait_not_verified_locally",
                     "download_statuses": [item["download_status"] for item in candidates]})
    report = {"source_id": SOURCE_ID, "checked_at": now_iso(), "performers": len(performers),
              "avatar_candidates": len(media), "downloaded_avatars": len(verified), "missing_avatars": len(gaps),
              "failed_downloads": sum(item["download_status"] == "failed" for item in media),
              "pending_downloads": sum(item["download_status"] == "pending" for item in media),
              "corrupt_or_missing_files": failures, "sqlite_integrity": integrity, "foreign_key_errors": len(foreign_keys),
              "full_site_coverage": False, "decode_validation": "see browser-report.json", "gaps": gaps}
    _write_json(output_dir / "data-audit.json", report)
    return report


def archive_jable(database: Path, output_dir: Path, *, evidence_dirs: list[Path] | None = None, acceptance_dir: Path | None = None) -> dict[str, Any]:
    """Export a portable, consistent Jable-only database plus verified local portraits."""
    import sqlite3
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "media").mkdir()
    source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    destination = connect(output_dir / "collector.db")
    copied = 0
    counts: dict[str, int] = {}
    try:
        source.execute("BEGIN")
        for table in ("collection_runs", "performers", "performer_observations", "performer_media_candidates"):
            rows = [dict(row) for row in source.execute(f"SELECT * FROM {table} WHERE source_id=?", (SOURCE_ID,))]
            if table == "performer_media_candidates":
                rows = [row for row in rows if row["purpose"] == "avatar"]
                for row in rows:
                    if row["download_status"] != "downloaded":
                        continue
                    original = Path(row["local_path"])
                    original = original if original.is_absolute() else database.parent / original
                    raw = original.read_bytes()
                    _, suffix, _, _ = inspect_image(raw)
                    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                        raise ValueError("Archive portrait checksum mismatch")
                    relative = f"media/{row['media_candidate_id']}{suffix}"
                    (output_dir / relative).write_bytes(raw)
                    row["local_path"] = relative
                    copied += 1
            counts[table] = len(rows)
            if rows:
                columns = list(rows[0])
                destination.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [tuple(row[column] for column in columns) for row in rows])
            (output_dir / f"{table}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        destination.commit()
    finally:
        destination.close()
        source.close()
    audit = audit_jable(output_dir / "collector.db", output_dir)
    from collector.pipeline import export_selfdeepsearch
    export_report = export_selfdeepsearch(output_dir / "collector.db", output_dir / "selfdeepsearch-export")
    (output_dir / "README.txt").write_text(
        "Jable 公开演员资料与普通头像归档\n\n"
        "collector.db：仅含 Jable 的演员、观测、采集运行及头像候选。\n"
        "media/：已核验 SHA-256 的原始普通头像，数据库采用相对路径。\n"
        "selfdeepsearch-export/：供后续接入的演员与媒体 staging JSONL，不是正式发布清单。\n"
        "data-audit.json：文件核验结果及每位演员的头像缺项。\n"
        "archive-manifest.json：文件大小和校验值。\n\n"
        "请整体保存或搬迁本目录，不要只复制数据库。使用代码仓库中的 build-performer-showcase 可重新生成离线预览。\n"
        "资料保持 needs_review / staging。此归档不保证全站完整，不包含成人视频或露骨图片。\n",
        encoding="utf-8",
    )
    for evidence_dir in evidence_dirs or []:
        evidence_root = evidence_dir.resolve()
        for source_file in evidence_root.rglob("*"):
            if not source_file.is_file() or source_file.name not in {"models.html", "manifest.json", "capture-report.json", "crawl-report.json", "robots.txt"}:
                continue
            if evidence_root not in source_file.resolve().parents:
                raise ValueError("Evidence file escapes its source directory")
            if source_file.name == "models.html":
                evidence = json.loads((source_file.parent / "capture-report.json").read_text(encoding="utf-8"))
                if evidence.get("sample_kind") != "sanitized_browser_dom_projection" or hashlib.sha256(source_file.read_bytes()).hexdigest() != evidence.get("projection_sha256"):
                    raise ValueError("Only checksum-verified sanitized browser projections may be archived")
            target = output_dir / "evidence" / evidence_root.name / source_file.relative_to(evidence_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
    if acceptance_dir:
        for name in ("coverage-report.json", "browser-report.json", "showcase-report.json"):
            source_file = acceptance_dir / name
            if source_file.is_file():
                shutil.copyfile(source_file, output_dir / name)
    files = [{"file": file.relative_to(output_dir).as_posix(), "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
             for file in sorted(output_dir.rglob("*")) if file.is_file() and file.suffix not in {"-wal", "-shm"} and not file.name.endswith(("-wal", "-shm"))]
    report = {"source_id": SOURCE_ID, "checked_at": now_iso(), "counts": counts, "copied_avatars": copied,
              "portable_relative_media_paths": True, "rights_status": "needs_review", "full_site_coverage": False,
              "sqlite_integrity": audit["sqlite_integrity"], "files": files}
    report["selfdeepsearch_export"] = export_report
    report["evidence_directories"] = [directory.name for directory in evidence_dirs or []]
    _write_json(output_dir / "archive-manifest.json", report)
    return report


def audit_jable_coverage(report_path: Path, output_dir: Path) -> dict[str, Any]:
    """Recount saved, checksum-verified pages across a resume chain."""
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed_root = report_path.resolve().parent.parent
    current: Path | None = report_path.resolve()
    visited_reports: set[Path] = set()
    records: dict[str, set[int]] = {}
    scanned: set[int] = set()
    occurrences = 0
    last_page = 1
    chain = []
    while current:
        if current in visited_reports or len(visited_reports) >= 50:
            raise ValueError("Invalid or excessive resume chain")
        if allowed_root not in current.parents:
            raise ValueError("Resume report is outside the capture root")
        visited_reports.add(current)
        crawl = json.loads(current.read_text(encoding="utf-8"))
        if crawl.get("source_id") != SOURCE_ID:
            raise ValueError("Coverage source mismatch")
        last_page = max(last_page, int(crawl.get("discovered_last_page", 1)))
        if last_page > 10000:
            raise ValueError("Excessive pagination range")
        pages = crawl.get("pages", [])
        if crawl.get("status") == "captured" and crawl.get("source_url") == "https://jable.tv/models/":
            pages = [{**crawl, "page": 1, "directory": str(current.parent)}]
        for page in pages:
            if page.get("status") != "captured" or page["page"] in scanned:
                continue
            sample_dir = Path(page["directory"]).resolve()
            if current.parent != sample_dir and current.parent not in sample_dir.parents:
                raise ValueError("Coverage sample escapes its report directory")
            raw = (sample_dir / "models.html").read_bytes()
            if hashlib.sha256(raw).hexdigest() != page["projection_sha256"]:
                raise ValueError("Coverage sample hash mismatch")
            items = parse_jable_html(raw.decode("utf-8"), source_url=page["source_url"], checked_at=page["checked_at"])
            occurrences += len(items)
            for item in items:
                records.setdefault(str(item["external_id"]), set()).add(page["page"])
            scanned.add(page["page"])
        chain.append(str(current))
        current = Path(crawl["resumed_from"]).resolve() if crawl.get("resumed_from") else None
    missing = [number for number in range(1, last_page + 1) if number not in scanned]
    report = {"source_id": SOURCE_ID, "checked_at": now_iso(), "captured_pages": sorted(scanned), "captured_page_count": len(scanned),
              "discovered_last_page": last_page, "pending_pages": missing, "listing_pages_complete": not missing,
              "source_record_occurrences": occurrences, "unique_performers_in_saved_pages": len(records),
              "duplicate_occurrences": occurrences - len(records), "full_site_coverage": False,
              "snapshot_consistency": "non_atomic_dynamic_listing", "resume_chain": chain,
              "records_seen_on_multiple_pages": [{"external_id": key, "pages": sorted(pages)} for key, pages in sorted(records.items()) if len(pages) > 1]}
    _write_json(output_dir / "coverage-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse, persist, download and preview Jable performer media")
    subparsers = parser.add_subparsers(dest="command", required=True)
    parse = subparsers.add_parser("parse-jable-samples")
    parse.add_argument("--manifest", type=Path, required=True)
    parse.add_argument("--output-dir", type=Path, required=True)
    ingest = subparsers.add_parser("ingest-jable-samples")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--database", type=Path, required=True)
    ingest.add_argument("--output-dir", type=Path, required=True)
    crawl = subparsers.add_parser("ingest-jable-crawl")
    crawl.add_argument("--report", type=Path, required=True)
    crawl.add_argument("--database", type=Path, required=True)
    audit = subparsers.add_parser("audit-jable")
    audit.add_argument("--database", type=Path, required=True)
    audit.add_argument("--output-dir", type=Path, required=True)
    archive = subparsers.add_parser("archive-jable")
    archive.add_argument("--database", type=Path, required=True)
    archive.add_argument("--output-dir", type=Path, required=True)
    archive.add_argument("--evidence-dir", type=Path, action="append", default=[])
    archive.add_argument("--acceptance-dir", type=Path)
    coverage = subparsers.add_parser("audit-jable-coverage")
    coverage.add_argument("--report", type=Path, required=True)
    coverage.add_argument("--output-dir", type=Path, required=True)
    download = subparsers.add_parser("download-jable-media")
    download.add_argument("--database", type=Path, required=True)
    download.add_argument("--output-dir", type=Path, required=True)
    download.add_argument("--max-images", type=int, default=20)
    download.add_argument("--acknowledgement", required=True)
    download.add_argument("--retry-transient", action="store_true", help="Retry only recorded connection/time-out failures; never retry HTTP 403/429")
    showcase = subparsers.add_parser("build-performer-showcase")
    showcase.add_argument("--database", type=Path, required=True)
    showcase.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "parse-jable-samples":
            result = parse_jable_manifest(args.manifest, args.output_dir)
        elif args.command == "ingest-jable-samples":
            result = ingest_jable_manifest(args.manifest, args.database, args.output_dir)
        elif args.command == "ingest-jable-crawl":
            result = ingest_jable_crawl(args.report, args.database)
        elif args.command == "audit-jable":
            result = audit_jable(args.database, args.output_dir)
        elif args.command == "archive-jable":
            result = archive_jable(args.database, args.output_dir, evidence_dirs=args.evidence_dir, acceptance_dir=args.acceptance_dir)
        elif args.command == "audit-jable-coverage":
            result = audit_jable_coverage(args.report, args.output_dir)
        elif args.command == "download-jable-media":
            result = download_jable_media(
                args.database, args.output_dir, max_images=args.max_images,
                acknowledgement=args.acknowledgement,
                retry_transient=args.retry_transient,
            )
        else:
            result = build_performer_showcase(args.database, args.output_dir)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "JABLE_ASSET_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
