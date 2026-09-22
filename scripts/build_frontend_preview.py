#!/usr/bin/env python3
"""Build an offline frontend preview from an existing actor collection snapshot."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required. Run through node scripts/run_python.mjs or the repository .venv.")
sys.path.insert(0, str(ROOT / "workers" / "collector-python"))

from collector.media import MAX_IMAGE_BYTES, inspect_image  # noqa: E402

BUILDER = "frontend-preview-v1"
DEFAULT_WORK_DIR = ROOT / "runtime/actor-collection/20260922-full"
DEFAULT_OUTPUT_DIR = ROOT / "runtime/frontend-preview/20260922"
TEMPLATE = ROOT / "scripts/templates/frontend-preview.html"
PROFILE_FIELDS = ("name", "profile_url", "avatar", "aliases", "biography", "birth_date", "height")
SOURCE_NAMES = {"jable_reference": "Jable", "javdb_reference": "JavDB"}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_timestamp(values) -> str | None:
    parsed = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                parsed.append((stamp, value))
        except ValueError:
            continue
    return max(parsed, key=lambda item: item[0])[1] if parsed else None


def public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not (parsed.username or parsed.password):
            return value
    except ValueError:
        pass
    return None


def project_provenance(value: object) -> dict:
    """Keep only public URLs and observation times for fields exposed by the page."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for field in (*PROFILE_FIELDS, "work_count"):
        evidence = value.get(field)
        if not isinstance(evidence, dict):
            continue
        safe = {
            "source_url": public_url(evidence.get("source_url")),
            "checked_at": latest_timestamp([evidence.get("checked_at")]),
        }
        safe = {key: item for key, item in safe.items() if item is not None}
        if safe:
            result[field] = safe
    return result


def copy_avatar(actor: dict, workdir: Path, media_dir: Path, warnings: list[dict]) -> dict | None:
    """Only copy verified image bytes from inside the selected data workspace."""
    for candidate in actor.get("avatars", []):
        if not isinstance(candidate, dict) or candidate.get("verified_locally") is not True or candidate.get("download_status") != "downloaded":
            continue
        try:
            identifier = candidate.get("media_candidate_id", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                raise ValueError("invalid_media_identifier")
            local_path = candidate.get("local_path")
            if not isinstance(local_path, str) or not local_path:
                raise ValueError("missing_media_path")
            source = Path(local_path)
            source = (source if source.is_absolute() else workdir / source).resolve()
            if not source.is_relative_to(workdir) or not source.is_file():
                raise ValueError("missing_or_external_media_path")
            if not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
                raise ValueError("invalid_media_size")
            with source.open("rb") as stream:
                raw = stream.read(MAX_IMAGE_BYTES + 1)
            if len(raw) > MAX_IMAGE_BYTES or len(raw) != candidate.get("byte_size"):
                raise ValueError("media_size_mismatch")
            if hashlib.sha256(raw).hexdigest() != candidate.get("sha256"):
                raise ValueError("media_hash_mismatch")
            mime, suffix, width, height = inspect_image(raw)
            if width < 1 or height < 1 or (mime, width, height) != (candidate.get("mime_type"), candidate.get("width"), candidate.get("height")):
                raise ValueError("media_metadata_mismatch")
            filename = f"{identifier}{suffix}"
            (media_dir / filename).write_bytes(raw)
            return {"url": f"media/{filename}", "width": width, "height": height}
        except (OSError, TypeError, ValueError) as exc:
            warnings.append({"actor_id": actor.get("performer_id"), "code": str(exc) if isinstance(exc, ValueError) else "media_read_failed"})
    return None


def build_dataset(workdir: Path, stage: Path) -> tuple[dict, list[dict]]:
    workdir = workdir.resolve()
    media_dir = stage / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    task_report = read_json(workdir / "task-report.json")
    collection_summary = read_json(workdir / "collection-summary.json")
    warnings: list[dict] = []
    actors = []
    seen = set()
    with (workdir / "exports/performers.jsonl").open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) and raw[key] for key in ("performer_id", "source_id", "external_id")):
                raise ValueError(f"Invalid actor identity on export line {number}")
            if raw["performer_id"] in seen:
                raise ValueError(f"Duplicate actor identity on export line {number}")
            seen.add(raw["performer_id"])
            avatar = copy_avatar(raw, workdir, media_dir, warnings)
            aliases = raw.get("aliases")
            if isinstance(aliases, str):
                aliases = [aliases] if aliases else []
            if not isinstance(aliases, list):
                aliases = []
            profile_url = public_url(raw.get("profile_url"))
            actor = {
                "id": raw["performer_id"], "source_id": raw["source_id"],
                "external_id": raw["external_id"], "name": raw.get("name") or "未记录姓名",
                "aliases": [value for value in aliases if isinstance(value, str) and value],
                "biography": raw.get("biography") if isinstance(raw.get("biography"), str) else None,
                "birth_date": raw.get("birth_date") if isinstance(raw.get("birth_date"), str) else None,
                "height": raw.get("height") if type(raw.get("height")) in (str, int, float) else None,
                "work_count": raw.get("work_count") if type(raw.get("work_count")) is int else None,
                "checked_at": raw.get("checked_at"),
                "avatar": avatar, "profile_url": profile_url,
                "field_provenance": project_provenance(raw.get("field_provenance")),
            }
            actor["missing_fields"] = [field for field in PROFILE_FIELDS if not (raw.get("name") if field == "name" else actor.get(field))]
            actors.append(actor)
    actors.sort(key=lambda actor: (actor["avatar"] is None, actor["name"].casefold(), actor["id"]))

    database = workdir / "tasks.db"
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        tasks = [dict(row) for row in connection.execute(
            "SELECT task_id AS id,source_id,kind,url,status,attempts,error_code,checked_at "
            "FROM tasks ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,priority,source_id,url"
        )]
        source_states = {row["source_id"]: dict(row) for row in connection.execute("SELECT source_id,blocked,error_code FROM source_state")}
    finally:
        connection.close()
    for task in tasks:
        task["url"] = public_url(task["url"])
        if task["url"] is None:
            raise ValueError(f"Invalid public task URL for task {task['id']}")

    statuses = Counter(task["status"] for task in tasks)
    counts = {
        "performers": len(actors), "avatars": sum(actor["avatar"] is not None for actor in actors),
        "missing_avatars": sum(actor["avatar"] is None for actor in actors),
        "observations": collection_summary.get("counts", {}).get("performer_observations"),
        "works": collection_summary.get("counts", {}).get("works"),
        "tasks_total": len(tasks), "tasks_completed": statuses["completed"],
        "tasks_blocked": statuses["blocked"], "tasks_pending": statuses["pending"],
        "missing_fields": {field: sum(field in actor["missing_fields"] for actor in actors) for field in PROFILE_FIELDS},
    }
    expected_tasks = Counter()
    for row in task_report.get("tasks", []):
        expected_tasks[row["status"]] += row["count"]
    if counts["performers"] != task_report.get("performers") or counts["avatars"] != task_report.get("avatars_downloaded") or statuses != expected_tasks:
        warnings.append({"code": "report_snapshot_differs_from_current_exports_or_queue"})

    latest_run = collection_summary.get("latest_run", {})
    latest_errors = {row["source_id"]: row for row in latest_run.get("errors", []) if isinstance(row, dict) and row.get("source_id")}
    javdb_diagnostic = collection_summary.get("javdb_network_diagnostic", {})
    if not isinstance(javdb_diagnostic, dict):
        javdb_diagnostic = {}
    source_ids = set(SOURCE_NAMES) | set(source_states) | {actor["source_id"] for actor in actors} | {task["source_id"] for task in tasks}
    sources = []
    for source_id in sorted(source_ids):
        state = source_states.get(source_id)
        error = latest_errors.get(source_id, {})
        checked_at = latest_timestamp(task["checked_at"] for task in tasks if task["source_id"] == source_id)
        connection_error = None
        diagnostic_at = latest_timestamp([javdb_diagnostic.get("checked_at")])
        if (source_id == "javdb_reference" and state and state["blocked"]
                and javdb_diagnostic.get("source_id") == source_id
                and diagnostic_at and latest_timestamp([checked_at, diagnostic_at]) == diagnostic_at
                and (javdb_diagnostic.get("errno") == 104 or javdb_diagnostic.get("error_type") == "ConnectionResetError")):
            connection_error = "connection_reset"
            checked_at = diagnostic_at
        sources.append({
            "id": source_id, "name": SOURCE_NAMES.get(source_id, source_id),
            "status": "blocked" if state and state["blocked"] else "ready" if state else "unknown",
            "error_code": state.get("error_code") if state else error.get("error_code"),
            "http_status": error.get("http_status") if state and state["blocked"] else None,
            "connection_error": connection_error, "checked_at": checked_at,
        })
    snapshot_at = latest_timestamp([task_report.get("checked_at"), collection_summary.get("checked_at"), *(task["checked_at"] for task in tasks)])
    dataset = {
        "schema_version": 1, "mode": "snapshot_preview", "snapshot_at": snapshot_at,
        "counts": counts, "actors": actors, "tasks": tasks, "sources": sources,
        "summary": {
            "status": collection_summary.get("status", "unknown"),
            "new_actors": collection_summary.get("new_performers"),
            "new_avatars": collection_summary.get("new_avatars"),
            "successful_pages": collection_summary.get("successful_pages"),
            "new_observations": collection_summary.get("new_content_observations"),
            "changed_fields": collection_summary.get("changed_fields", {}),
            "started_at": latest_run.get("started_at"), "completed_at": latest_run.get("completed_at"),
            "all_data_collected": collection_summary.get("all_data_collected") is True,
            "full_site_coverage": task_report.get("full_site_coverage") is True,
        },
    }
    return dataset, warnings


def build_preview(workdir: Path, output: Path, template_path: Path = TEMPLATE) -> dict:
    workdir = workdir.resolve()
    output = output.absolute()
    if output.is_symlink():
        raise ValueError("Output must not be a symbolic link")
    resolved_output = output.resolve()
    if resolved_output.is_relative_to(workdir) or workdir.is_relative_to(resolved_output):
        raise ValueError("Output must be separate from the source data workspace")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        marker = output / "build-report.json"
        if not marker.is_file() or read_json(marker).get("builder") != BUILDER:
            raise ValueError("Existing output is not a generated frontend preview; choose a new directory")
    template = template_path.read_text(encoding="utf-8")
    if template.count("__DATASET__") != 1:
        raise ValueError("Expected exactly one __DATASET__ placeholder in frontend preview template")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frontend-preview-", dir=output.parent) as temporary:
        stage = Path(temporary) / "preview"
        stage.mkdir()
        dataset, warnings = build_dataset(workdir, stage)
        encoded = json.dumps(dataset, ensure_ascii=False, separators=(",", ":"))
        safe_json = encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        (stage / "index.html").write_text(template.replace("__DATASET__", safe_json), encoding="utf-8")
        write_json(stage / "dataset.json", dataset)
        report = {
            "builder": BUILDER, "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "snapshot_at": dataset["snapshot_at"], "counts": dataset["counts"],
            "network_requests": 0, "database_access": "read_only", "warnings": warnings,
            "dataset_bytes": (stage / "dataset.json").stat().st_size,
            "page_bytes": (stage / "index.html").stat().st_size,
            "copied_avatar_files": len(list((stage / "media").iterdir())),
        }
        write_json(stage / "build-report.json", report)
        if not output.exists():
            stage.replace(output)
        else:
            media = output / "media"
            if media.is_symlink():
                raise ValueError("Generated media directory must not be a symbolic link")
            # Only replace the four generated artifacts; leave unrelated files intact.
            if media.exists():
                shutil.rmtree(media)
            (stage / "media").replace(media)
            for name in ("index.html", "dataset.json", "build-report.json"):
                (stage / name).replace(output / name)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    try:
        report = build_preview(args.work_dir, args.output_dir)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
