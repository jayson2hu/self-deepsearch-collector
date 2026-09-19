from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

ENTITY_NAMESPACE = uuid.UUID("632cf997-f449-4e86-8d82-2345f0e10921")

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    discovered_count INTEGER NOT NULL DEFAULT 0,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    parsed_count INTEGER NOT NULL DEFAULT 0,
    stored_count INTEGER NOT NULL DEFAULT 0,
    media_candidate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS works (
    work_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    canonical_code TEXT NOT NULL,
    title TEXT NOT NULL,
    release_date TEXT,
    studio_name TEXT,
    current_content_hash TEXT NOT NULL,
    rights_status TEXT NOT NULL DEFAULT 'needs_review',
    publication_status TEXT NOT NULL DEFAULT 'staging',
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS work_observations (
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    candidate_json TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, external_id, content_hash)
);

CREATE TABLE IF NOT EXISTS performer_aliases (
    work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    source_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (work_id, alias)
);

CREATE TABLE IF NOT EXISTS performers (
    performer_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    current_content_hash TEXT NOT NULL,
    rights_status TEXT NOT NULL DEFAULT 'needs_review',
    publication_status TEXT NOT NULL DEFAULT 'staging',
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS performer_observations (
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    candidate_json TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, external_id, content_hash)
);

CREATE TABLE IF NOT EXISTS performer_media_candidates (
    media_candidate_id TEXT PRIMARY KEY,
    performer_id TEXT NOT NULL REFERENCES performers(performer_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    candidate_url TEXT NOT NULL,
    source_page_url TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('avatar', 'gallery')),
    display_position INTEGER NOT NULL,
    rights_status TEXT NOT NULL CHECK (rights_status IN ('needs_review', 'allowed', 'restricted', 'takedown')),
    download_status TEXT NOT NULL CHECK (download_status IN ('pending', 'downloaded', 'rejected', 'failed')),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
    local_path TEXT,
    sha256 TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    byte_size INTEGER,
    checked_at TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (performer_id, candidate_url)
);

CREATE INDEX IF NOT EXISTS performer_media_queue_idx
ON performer_media_candidates (rights_status, review_status, download_status, purpose, display_position);

CREATE TABLE IF NOT EXISTS media_candidates (
    media_candidate_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    candidate_url TEXT NOT NULL UNIQUE,
    source_page_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('cover', 'gallery', 'avatar')),
    source_position INTEGER NOT NULL,
    display_position INTEGER,
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    display_eligible INTEGER NOT NULL CHECK (display_eligible IN (0, 1)),
    rights_status TEXT NOT NULL CHECK (rights_status IN ('needs_review', 'allowed', 'restricted', 'takedown')),
    download_status TEXT NOT NULL CHECK (download_status IN ('pending', 'downloaded', 'rejected', 'failed')),
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'approved', 'rejected')),
    local_path TEXT,
    sha256 TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    byte_size INTEGER,
    checked_at TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS media_candidates_queue_idx
ON media_candidates (rights_status, review_status, download_status, purpose, source_position);

CREATE TABLE IF NOT EXISTS collection_errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    source_url TEXT,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def entity_id(source_id: str, external_id: str) -> str:
    return str(uuid.uuid5(ENTITY_NAMESPACE, f"{source_id}:work:{external_id}"))


def performer_entity_id(source_id: str, external_id: str) -> str:
    return str(uuid.uuid5(ENTITY_NAMESPACE, f"{source_id}:performer:{external_id}"))


def start_run(connection: sqlite3.Connection, *, source_id: str, connector_version: str, checked_at: str, discovered_count: int) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO collection_runs (run_id, source_id, connector_version, checked_at, started_at, status, discovered_count) VALUES (?, ?, ?, ?, ?, 'running', ?)",
        (run_id, source_id, connector_version, checked_at, now_iso(), discovered_count),
    )
    connection.commit()
    return run_id


def record_error(connection: sqlite3.Connection, run_id: str, *, source_url: str | None, error_code: str, message: str) -> None:
    connection.execute(
        "INSERT INTO collection_errors (run_id, source_url, error_code, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, source_url, error_code, message[:2000], now_iso()),
    )
    connection.execute("UPDATE collection_runs SET error_count = error_count + 1 WHERE run_id = ?", (run_id,))
    connection.commit()


def ingest_candidate(connection: sqlite3.Connection, run_id: str, candidate: dict[str, Any], media: Iterable[dict[str, Any]]) -> str:
    payload = candidate["payload"]
    if not isinstance(payload, dict):
        raise ValueError("candidate payload must be an object")
    source_id = str(candidate["source_id"])
    external_id = str(candidate["external_id"])
    work_id = entity_id(source_id, external_id)
    timestamp = now_iso()
    checked_at = str(candidate["provenance"]["checked_at"])
    connection.execute(
        """
        INSERT INTO works (work_id, source_id, external_id, canonical_code, title, release_date, studio_name, current_content_hash, checked_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, external_id) DO UPDATE SET
          canonical_code=excluded.canonical_code, title=excluded.title, release_date=excluded.release_date,
          studio_name=excluded.studio_name, current_content_hash=excluded.current_content_hash,
          checked_at=excluded.checked_at, updated_at=excluded.updated_at
        """,
        (work_id, source_id, external_id, payload["canonical_code"], payload["title"], payload.get("release_date"), payload.get("studio_name"), candidate["content_hash"], checked_at, timestamp, timestamp),
    )
    connection.execute(
        "INSERT OR IGNORE INTO work_observations (source_id, external_id, content_hash, run_id, candidate_json, checked_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_id, external_id, candidate["content_hash"], run_id, json.dumps(candidate, ensure_ascii=False, separators=(",", ":")), checked_at, timestamp),
    )
    connection.execute("DELETE FROM performer_aliases WHERE work_id = ?", (work_id,))
    for alias in payload.get("performer_aliases", []):
        connection.execute(
            "INSERT OR IGNORE INTO performer_aliases (work_id, alias, source_id, checked_at) VALUES (?, ?, ?, ?)",
            (work_id, alias, source_id, checked_at),
        )
    media_count = 0
    for item in media:
        media_count += 1
        connection.execute(
            """
            INSERT INTO media_candidates (
              media_candidate_id, work_id, source_id, external_id, candidate_url, source_page_url, source_type,
              purpose, source_position, display_position, is_primary, display_eligible, rights_status,
              download_status, review_status, checked_at, connector_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_candidate_id) DO UPDATE SET
              display_position=excluded.display_position, display_eligible=excluded.display_eligible,
              checked_at=excluded.checked_at, connector_version=excluded.connector_version, updated_at=excluded.updated_at
            """,
            (
                item["media_candidate_id"], work_id, source_id, external_id, item["candidate_url"], item["source_page_url"], item["source_type"],
                item["purpose"], item["source_position"], item["display_position"], int(bool(item["is_primary"])), int(bool(item["display_eligible"])),
                item["rights_status"], item["download_status"], item["review_status"], item["checked_at"], item["connector_version"], timestamp, timestamp,
            ),
        )
    connection.execute("UPDATE collection_runs SET parsed_count = parsed_count + 1, stored_count = stored_count + 1, media_candidate_count = media_candidate_count + ? WHERE run_id = ?", (media_count, run_id))
    connection.commit()
    return work_id


def complete_run(connection: sqlite3.Connection, run_id: str, *, fetched_count: int, failed: bool = False) -> None:
    connection.execute(
        "UPDATE collection_runs SET fetched_count = ?, completed_at = ?, status = ? WHERE run_id = ?",
        (fetched_count, now_iso(), "failed" if failed else "completed", run_id),
    )
    connection.commit()


def database_stats(connection: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "collection_runs", "works", "work_observations", "performer_aliases", "media_candidates",
        "performers", "performer_observations", "performer_media_candidates", "collection_errors",
    )
    return {table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}
