#!/usr/bin/env python3
"""Restore verified local performer data into a new, portable working directory."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required. Use the repository .venv or npm run data:prepare.")
sys.path.insert(0, str(ROOT / "workers" / "collector-python"))

from collector.jable_assets import audit_jable, build_performer_showcase, load_current_performer_payloads  # noqa: E402
from collector.pipeline import PERFORMER_PROFILE_FIELDS, export_selfdeepsearch  # noqa: E402

SOURCE_ID = "jable_reference"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_file(root: Path, name: str) -> Path:
    """Accept only portable relative file names whose resolved paths stay inside root."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid archive file path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
        raise ValueError(f"Unsafe archive file path: {name}")
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or escaped archive file: {name}")
    return path


def verify_archive(root: Path) -> tuple[dict, dict[str, dict], str]:
    manifest_path = archive_file(root, "archive-manifest.json")
    manifest_hash = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_id") != SOURCE_ID or not isinstance(manifest.get("files"), list):
        raise ValueError("Expected a Jable archive manifest with a file list")
    files = {}
    for item in manifest["files"]:
        name = item["file"]
        if name in files or type(item.get("bytes")) is not int or item["bytes"] < 0:
            raise ValueError(f"Duplicate file or invalid byte count: {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            raise ValueError(f"Invalid SHA-256: {name}")
        path = archive_file(root, name)
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError(f"Archive verification failed: {name}")
        files[name] = item
    if "collector.db" not in files:
        raise ValueError("Archive manifest does not include collector.db")
    return manifest, files, manifest_hash


def read_database(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def time_range(values: list[str]) -> dict:
    ordered = sorted(set(values), key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
    return {"first_checked_at": ordered[0] if ordered else None,
            "last_checked_at": ordered[-1] if ordered else None}


def prepare(archive: Path, output: Path) -> dict:
    archive = archive.resolve()
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists; choose a new directory: {output}")
    if output.resolve().is_relative_to(archive):
        raise ValueError("Output must not be inside the source archive")
    manifest, verified_files, manifest_hash = verify_archive(archive)
    prepared_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".prepare-primary-", dir=output.parent) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        source = read_database(archive / "collector.db")
        destination = sqlite3.connect(stage / "collector.db")
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        database = stage / "collector.db"
        connection = read_database(database)
        try:
            tables = {
                table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in ("collection_runs", "performers", "performer_observations", "performer_media_candidates")
            }
            works = connection.execute("SELECT COUNT(*) FROM works").fetchone()[0]
            if works or any(row["source_id"] != SOURCE_ID for rows in tables.values() for row in rows):
                raise ValueError("Expected a Jable performer-only archive")
            performers = [dict(row) for row in connection.execute("""
                SELECT p.*, o.candidate_json, o.checked_at AS observation_checked_at
                FROM performers p LEFT JOIN performer_observations o
                  ON o.source_id=p.source_id AND o.external_id=p.external_id
                 AND o.content_hash=p.current_content_hash
                ORDER BY p.name, p.external_id
            """)]
            current_payloads = load_current_performer_payloads(connection, source_id=SOURCE_ID)
        finally:
            connection.close()

        media_by_performer = {}
        for media in tables["performer_media_candidates"]:
            if media["purpose"] != "avatar":
                raise ValueError("Only ordinary performer avatars are supported")
            if media["download_status"] != "downloaded":
                continue
            name = media["local_path"]
            if name not in verified_files or not name.startswith("media/"):
                raise ValueError("Downloaded avatar is not covered by the archive manifest")
            original = archive_file(archive, name)
            if media["sha256"] != verified_files[name]["sha256"]:
                raise ValueError(f"Avatar database hash differs from manifest: {name}")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
            media_by_performer.setdefault(media["performer_id"], media)
        (stage / "media").mkdir(exist_ok=True)

        for table, rows in tables.items():
            (stage / f"{table}.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        export_report = export_selfdeepsearch(database, stage / "selfdeepsearch-export")
        audit = audit_jable(database, stage)
        audit["decode_validation"] = (
            "Separate browser check: preview/browser-report.json is created only by test:jable-showcase"
        )
        write_json(stage / "data-audit.json", audit)
        if audit["sqlite_integrity"] != "ok" or audit["foreign_key_errors"] or audit["corrupt_or_missing_files"]:
            raise ValueError("Restored database or avatar integrity check failed")
        preview = stage / "preview"
        preview.mkdir()
        if "coverage-report.json" in verified_files:
            shutil.copyfile(archive / "coverage-report.json", preview / "coverage-report.json")
        showcase = build_performer_showcase(database, preview)
        showcase["output_dir"] = "preview"
        write_json(preview / "showcase-report.json", showcase)

        profiles = []
        for performer in performers:
            candidate_json = performer.pop("candidate_json")
            if not candidate_json:
                raise ValueError(f"Current observation missing: {performer['performer_id']}")
            candidate = json.loads(candidate_json)
            current = current_payloads.get(performer["external_id"], {"payload": {}, "field_provenance": {}})
            avatar = media_by_performer.get(performer["performer_id"], {})
            profiles.append({
                **performer,
                **{key: current["payload"][key] for key in PERFORMER_PROFILE_FIELDS if key in current["payload"]},
                "work_count": current["payload"].get("work_count"),
                "aliases": current["payload"].get("aliases", []),
                "field_provenance": current["field_provenance"],
                "source_page_url": candidate.get("provenance", {}).get("source_url"),
                "avatar_path": avatar.get("local_path"),
                "avatar_sha256": avatar.get("sha256"),
                "avatar_media_candidate_id": avatar.get("media_candidate_id"),
                "avatar_width": avatar.get("width"), "avatar_height": avatar.get("height"),
                "avatar_byte_size": avatar.get("byte_size"),
            })
        profile_dir = stage / "profiles"
        profile_dir.mkdir()
        (profile_dir / "performers.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in profiles), encoding="utf-8")
        with (profile_dir / "performers.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            fields = list(dict.fromkeys(key for profile in profiles for key in profile)) if profiles else ["performer_id", "source_id", "external_id", "name"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows({key: json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (dict, list)) else value
                              for key, value in profile.items()} for profile in profiles)

        # Consolidate local writes before publishing a portable database without WAL sidecars.
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        inventory = {
            "format_version": 1, "source_id": SOURCE_ID, "prepared_at": prepared_at,
            "acquisition_mode": "restore_verified_local_archive", "network_requests": 0,
            "input_archive": str(archive), "input_manifest_sha256": manifest_hash,
            "input_database_sha256": verified_files["collector.db"]["sha256"],
            "input_archive_checked_at": manifest.get("checked_at"),
            "input_verified_files": len(verified_files),
            "source_collection_period": time_range([row["checked_at"] for row in tables["performer_observations"]]),
            "current_record_period": time_range([row["checked_at"] for row in profiles]),
            "output_directory": str(output),
            "counts": {**{table: len(rows) for table, rows in tables.items()},
                       "works": works, "downloaded_avatars": audit["downloaded_avatars"],
                       "missing_avatars": audit["missing_avatars"],
                       "missing_work_count": sum(row["work_count"] is None for row in profiles),
                       "missing_source_page_url": sum(not row["source_page_url"] for row in profiles)},
            "files": {"database": "collector.db", "profiles_jsonl": "profiles/performers.jsonl",
                      "profiles_csv": "profiles/performers.csv", "avatars": "media/",
                      "preview": "preview/index.html", "audit": "data-audit.json",
                      "platform_export": "selfdeepsearch-export/", "checksums": "files-manifest.json"},
            "gaps": {"avatars": "data-audit.json", "full_site_coverage": False,
                     "javdb_reference": {"available_in_restored_dataset": False, "works": 0, "performers": 0,
                                         "reason": "No JavDB database or source samples are supplied by this archive; historical acceptance counts are not restored data."}},
            "evidence_directory": str(archive / "evidence"),
            "evidence_copied": False, "rights_status": "needs_review", "publication_status": "staging",
            "export_report": export_report,
            "notes": ["prepared_at is local preparation time, not a new source collection time.",
                      "Source checked_at values are preserved exactly.",
                      "Profiles retain the latest observed nonempty fields; field_provenance records each field's original evidence.",
                      "current_content_hash identifies a source observation, not the combined profile export; structured CSV fields use JSON.",
                      "avatar_path is relative to the dataset root, not the profiles directory.",
                      "Avatar gaps and download problems are distinguished in data-audit.json.",
                      "Preview images have metadata/hash checks; browser decoding requires the separate showcase test."],
        }
        write_json(stage / "inventory.json", inventory)
        (stage / "README.txt").write_text(
            "本目录由已校验的本地归档恢复；本次没有重新访问来源网站。\n"
            "profiles/performers.jsonl 与 performers.csv 包含当前演员资料、列表作品数和普通头像路径。\n"
            "头像路径相对于本目录；迁移时请整体复制 collector.db、media/ 和导出文件。\n"
            "inventory.json 记录原始采集时段、恢复时间、数量、缺项与来源证据位置。\n"
            "本次没有恢复 JavDB 作品或演员数据。全部资料维持 needs_review / staging。\n",
            encoding="utf-8")
        write_json(stage / "files-manifest.json", {"files": [
            {"file": path.relative_to(stage).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(stage.rglob("*")) if path.is_file()
        ]})
        if output.exists() or output.is_symlink():
            raise ValueError(f"Output appeared during preparation; refusing to overwrite: {output}")
        stage.rename(output)
    return {"output_dir": str(output), "input_verified_files": len(verified_files),
            **inventory["counts"], "network_requests": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "runtime/jable-archive/20260919-final")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "runtime/primary-data" / datetime.now().astimezone().strftime("%Y%m%d"))
    args = parser.parse_args()
    try:
        report = prepare(args.archive_dir, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": "completed", **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
