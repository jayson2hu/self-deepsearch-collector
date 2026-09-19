from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from collector.connectors.javdb import CONNECTOR_VERSION, SOURCE_ID, extract_media_candidates
from collector.contracts import ContractError
from collector.samples import parse_manifest
from collector.storage import complete_run, connect, database_stats, ingest_candidate, start_run


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ingest_samples(manifest_path: Path, database: Path, output_dir: Path) -> dict[str, Any]:
    report = parse_manifest(manifest_path, output_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidates = _read_jsonl(output_dir / "candidates.jsonl")
    if len(candidates) != len(manifest["samples"]):
        raise ContractError("candidate count does not match sample manifest")

    all_media: list[dict[str, Any]] = []
    media_by_external: dict[str, list[dict[str, Any]]] = {}
    for item, candidate in zip(manifest["samples"], candidates, strict=True):
        sample_path = (manifest_path.parent / item["file"]).resolve()
        media = extract_media_candidates(
            sample_path.read_text(encoding="utf-8"), source_url=item["url"], checked_at=manifest["checked_at"],
        )
        external_id = str(candidate["external_id"])
        media_by_external[external_id] = media
        all_media.extend(media)

    (output_dir / "media_candidates.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in all_media), encoding="utf-8",
    )

    connection = connect(database)
    try:
        run_id = start_run(
            connection, source_id=SOURCE_ID, connector_version=CONNECTOR_VERSION,
            checked_at=manifest["checked_at"], discovered_count=len(candidates),
        )
        for candidate in candidates:
            ingest_candidate(connection, run_id, candidate, media_by_external[str(candidate["external_id"])])
        complete_run(connection, run_id, fetched_count=len(candidates))
        stats = database_stats(connection)
    finally:
        connection.close()

    result = {
        **report,
        "run_id": run_id,
        "database": str(database),
        "media_candidates": len(all_media),
        "database_stats": stats,
    }
    (output_dir / "ingest-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def export_selfdeepsearch(database: Path, output_dir: Path) -> dict[str, Any]:
    connection = connect(database)
    try:
        works = [dict(row) for row in connection.execute(
            "SELECT work_id, source_id, external_id, canonical_code, title, release_date, studio_name, current_content_hash, rights_status, publication_status, checked_at FROM works ORDER BY canonical_code"
        )]
        aliases: dict[str, list[str]] = {}
        for row in connection.execute("SELECT work_id, alias FROM performer_aliases ORDER BY work_id, alias"):
            aliases.setdefault(row["work_id"], []).append(row["alias"])
        media_rows = [dict(row) for row in connection.execute(
            """
            SELECT media_candidate_id, work_id, source_id, external_id, candidate_url, source_page_url,
                   source_type, purpose, source_position, display_position, is_primary, display_eligible,
                   rights_status, download_status, review_status, local_path, sha256, mime_type,
                   width, height, byte_size, checked_at, connector_version
            FROM media_candidates ORDER BY external_id, purpose, source_position
            """
        )]
        performers = [dict(row) for row in connection.execute(
            """
            SELECT performer_id, source_id, external_id, name, profile_url, current_content_hash,
              rights_status, publication_status, checked_at
            FROM performers ORDER BY source_id, name
            """
        )]
        performer_media_rows = [dict(row) for row in connection.execute(
            """
            SELECT media_candidate_id, performer_id, source_id, external_id, candidate_url,
              source_page_url, purpose, display_position, rights_status, download_status,
              review_status, local_path, sha256, mime_type, width, height, byte_size,
              checked_at, connector_version
            FROM performer_media_candidates ORDER BY source_id, external_id, purpose, display_position
            """
        )]
        for work in works:
            work["performer_aliases"] = aliases.get(work["work_id"], [])
    finally:
        connection.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "works.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in works), encoding="utf-8")
    (output_dir / "performers.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in performers), encoding="utf-8")
    staging = []
    for item in media_rows:
        staging.append({
            "media_candidate_id": item["media_candidate_id"],
            "entity_type": "work",
            "entity_id": item["work_id"],
            "source_id": item["source_id"],
            "source_external_id": item["external_id"],
            "candidate_url": item["candidate_url"],
            "source_page_url": item["source_page_url"],
            "source_type": item["source_type"],
            "purpose": item["purpose"],
            "position": item["display_position"],
            "is_primary": bool(item["is_primary"]),
            "display_eligible": bool(item["display_eligible"]),
            "rights_status": item["rights_status"],
            "download_status": item["download_status"],
            "review_status": item["review_status"],
            "content_hash": f"sha256:{item['sha256']}" if item["sha256"] else None,
            "mime_type": item["mime_type"],
            "byte_size": item["byte_size"],
            "width": item["width"],
            "height": item["height"],
            "checked_at": item["checked_at"],
            "connector_version": item["connector_version"],
        })
    (output_dir / "media_staging.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in staging), encoding="utf-8")
    performer_staging = []
    for item in performer_media_rows:
        performer_staging.append({
            "media_candidate_id": item["media_candidate_id"],
            "entity_type": "performer",
            "entity_id": item["performer_id"],
            "source_id": item["source_id"],
            "source_external_id": item["external_id"],
            "candidate_url": item["candidate_url"],
            "source_page_url": item["source_page_url"],
            "source_type": "web_page",
            "purpose": item["purpose"],
            "position": item["display_position"],
            "is_primary": item["purpose"] == "avatar" and item["display_position"] == 0,
            "display_eligible": True,
            "rights_status": item["rights_status"],
            "download_status": item["download_status"],
            "review_status": item["review_status"],
            "content_hash": f"sha256:{item['sha256']}" if item["sha256"] else None,
            "mime_type": item["mime_type"],
            "byte_size": item["byte_size"],
            "width": item["width"],
            "height": item["height"],
            "checked_at": item["checked_at"],
            "connector_version": item["connector_version"],
        })
    (output_dir / "performer_media_staging.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in performer_staging), encoding="utf-8",
    )
    report = {
        "works": len(works),
        "media_candidates": len(media_rows),
        "display_eligible_media": sum(bool(item["display_eligible"]) for item in media_rows),
        "downloaded_media": sum(item["download_status"] == "downloaded" for item in media_rows),
        "publication_ready_media": sum(item["rights_status"] == "allowed" and item["review_status"] == "approved" for item in media_rows),
        "performers": len(performers),
        "performer_media_candidates": len(performer_media_rows),
        "downloaded_performer_media": sum(item["download_status"] == "downloaded" for item in performer_media_rows),
        "publication_ready_performer_media": sum(item["rights_status"] == "allowed" and item["review_status"] == "approved" for item in performer_media_rows),
        "note": "media_staging is an intake artifact; final self-deepsearch display still requires media-python/1 renditions and publication approval",
    }
    (output_dir / "export-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persist and export collected source data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest-samples")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--database", type=Path, required=True)
    ingest.add_argument("--output-dir", type=Path, required=True)
    export = subparsers.add_parser("export-selfdeepsearch")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = ingest_samples(args.manifest, args.database, args.output_dir) if args.command == "ingest-samples" else export_selfdeepsearch(args.database, args.output_dir)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ContractError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "PIPELINE_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
