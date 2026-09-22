from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from collector.connectors.javdb_actors import CONNECTOR_VERSION, SOURCE_ID, parse_javdb_actor_list
from collector.media import MAX_IMAGE_BYTES, NoRedirect, inspect_image
from collector.storage import complete_run, connect, now_iso, performer_entity_id, start_run


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ingest_javdb_actor_snapshot(
    input_path: Path,
    *,
    source_url: str,
    checked_at: str,
    database: Path,
    output_dir: Path,
) -> dict[str, object]:
    raw = input_path.read_bytes()
    candidates = parse_javdb_actor_list(raw.decode("utf-8"), source_url=source_url, checked_at=checked_at)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "performers.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates), encoding="utf-8",
    )
    report = {
        **ingest_javdb_actor_candidates(candidates, database, checked_at=checked_at),
        "input_file": str(input_path),
        "input_bytes": len(raw),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
    }
    _write_json(output_dir / "ingest-report.json", report)
    return report


def _observation_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    return timestamp.astimezone(UTC)


def ingest_javdb_actor_candidates(
    candidates: list[dict[str, object]],
    database: Path,
    *,
    checked_at: str,
) -> dict[str, object]:
    """Store parsed actor candidates without requiring a raw HTML file on disk.

    Candidate payloads remain exact source observations. Historical aliases and
    avatars belong to the observation/media tables and are never copied into a
    newer candidate to fill missing fields.
    """
    observed_at = _observation_time(checked_at)
    for candidate in candidates:
        if candidate.get("source_id") != SOURCE_ID or candidate.get("entity_type") != "performer":
            raise ValueError("expected parsed JavDB performer candidates")
        if candidate.get("provenance", {}).get("checked_at") != checked_at:
            raise ValueError("candidate checked_at must match the ingestion checked_at")
    connection = connect(database)
    new_observations = 0
    try:
        run_id = start_run(
            connection, source_id=SOURCE_ID, connector_version=CONNECTOR_VERSION,
            checked_at=checked_at, discovered_count=len(candidates),
        )
        for candidate in candidates:
            timestamp = now_iso()
            external_id = str(candidate["external_id"])
            performer_id = performer_entity_id(SOURCE_ID, external_id)
            payload = candidate["payload"]
            previous = connection.execute(
                "SELECT checked_at FROM performers WHERE source_id=? AND external_id=?",
                (SOURCE_ID, external_id),
            ).fetchone()
            is_current = previous is None or observed_at >= _observation_time(previous["checked_at"])
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
                WHERE ?
                """,
                (performer_id, SOURCE_ID, external_id, payload["name"], payload["profile_url"],
                 candidate["content_hash"], checked_at, timestamp, timestamp, is_current),
            )
            observation = connection.execute(
                """
                INSERT OR IGNORE INTO performer_observations (
                  source_id, external_id, content_hash, run_id, candidate_json, checked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (SOURCE_ID, external_id, candidate["content_hash"], run_id,
                 json.dumps(candidate, ensure_ascii=False, separators=(",", ":")), checked_at, timestamp),
            )
            new_observations += observation.rowcount
            for item in candidate["media"]:
                connection.execute(
                    """
                    INSERT INTO performer_media_candidates (
                      media_candidate_id, performer_id, source_id, external_id, candidate_url,
                      source_page_url, purpose, display_position, rights_status, download_status,
                      review_status, checked_at, connector_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(media_candidate_id) DO UPDATE SET
                      checked_at=excluded.checked_at, connector_version=excluded.connector_version,
                      updated_at=excluded.updated_at
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
            "avatar_candidates": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=? AND purpose='avatar'", (SOURCE_ID,)).fetchone()[0]),
        }
    finally:
        connection.close()
    return {
        "source_id": SOURCE_ID,
        "connector_version": CONNECTOR_VERSION,
        "run_id": run_id,
        "database": str(database),
        "parsed_this_run": len(candidates),
        "new_observations_this_run": new_observations,
        **counts,
    }


def download_javdb_avatars(
    database: Path,
    output_dir: Path,
    *,
    max_images: int,
    acknowledgement: str,
) -> dict[str, object]:
    if acknowledgement != "internal_review_only":
        raise ValueError("acknowledgement must be internal_review_only")
    if not 1 <= max_images <= 50:
        raise ValueError("max_images must be between 1 and 50")
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = connect(database)
    rows = connection.execute(
        """
        SELECT media_candidate_id, candidate_url, source_page_url
        FROM performer_media_candidates
        WHERE source_id=? AND purpose='avatar' AND download_status='pending'
        ORDER BY external_id LIMIT ?
        """,
        (SOURCE_ID, max_images),
    ).fetchall()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())
    downloaded = []
    errors = []
    try:
        for index, row in enumerate(rows):
            if index:
                time.sleep(2)
            parsed = urlparse(row["candidate_url"])
            if parsed.scheme != "https" or parsed.hostname != "c0.jdbstatic.com":
                raise ValueError("JavDB avatar URL is outside the approved host")
            if not parsed.path.startswith("/avatars/") or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("JavDB avatar URL is not an approved public avatar path")
            request = urllib.request.Request(
                row["candidate_url"],
                headers={
                    "User-Agent": "self-deepsearch-collector/0.4 internal-review",
                    "Referer": row["source_page_url"],
                },
            )
            try:
                with opener.open(request, timeout=20) as response:
                    if response.status != 200:
                        raise ValueError(f"unexpected HTTP {response.status}")
                    raw = response.read(MAX_IMAGE_BYTES + 1)
                if len(raw) > MAX_IMAGE_BYTES:
                    raise ValueError("avatar exceeds 10 MiB")
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
                errors.append({"media_candidate_id": row["media_candidate_id"], "message": str(exc)})
        totals = {
            "downloaded": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=? AND purpose='avatar' AND download_status='downloaded'", (SOURCE_ID,)).fetchone()[0]),
            "failed": int(connection.execute("SELECT COUNT(*) FROM performer_media_candidates WHERE source_id=? AND purpose='avatar' AND download_status='failed'", (SOURCE_ID,)).fetchone()[0]),
        }
    finally:
        connection.close()
    report = {
        "requested_this_run": len(rows), "downloaded_this_run": len(downloaded),
        "failed_this_run": len(errors), "database_totals": totals,
        "staging_only": True, "rights_status": "needs_review",
        "items_this_run": downloaded, "errors_this_run": errors,
    }
    _write_json(output_dir / "download-report.json", report)
    return report


def build_actor_showcase(database: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / "media"
    media_dir.mkdir(exist_ok=True)
    connection = connect(database)
    try:
        performers = [dict(row) for row in connection.execute(
            """
            SELECT performer_id, external_id, name, profile_url, rights_status,
              publication_status, checked_at FROM performers WHERE source_id=? ORDER BY name
            """,
            (SOURCE_ID,),
        )]
        avatars = {row["performer_id"]: dict(row) for row in connection.execute(
            """
            SELECT performer_id, media_candidate_id, local_path, sha256, mime_type, width,
              height, byte_size, rights_status, review_status
            FROM performer_media_candidates
            WHERE source_id=? AND purpose='avatar' AND download_status='downloaded'
            """,
            (SOURCE_ID,),
        )}
    finally:
        connection.close()
    for performer in performers:
        performer["avatar"] = None
        avatar = avatars.get(performer["performer_id"])
        if avatar:
            source = Path(avatar["local_path"])
            target = media_dir / f"{avatar['media_candidate_id']}{source.suffix.lower()}"
            shutil.copyfile(source, target)
            performer["avatar"] = {**avatar, "url": f"media/{target.name}"}
    dataset = {
        "source_id": SOURCE_ID,
        "real_data": True,
        "publication_mode": "internal_acceptance_only",
        "counts": {"performers": len(performers), "avatars": sum(item["avatar"] is not None for item in performers)},
        "performers": performers,
    }
    _write_json(output_dir / "performers.json", dataset)
    cards = []
    for item in performers:
        image = f'<img src="{html.escape(item["avatar"]["url"])}" alt="{html.escape(item["name"])} 头像">' if item["avatar"] else f'<div class="placeholder">{html.escape(item["name"][:1])}</div>'
        cards.append(f'''<article class="card" data-search="{html.escape((item["name"] + " " + item["external_id"]).lower(), quote=True)}">{image}<div class="body"><span>JavDB 公开演员资料</span><h2>{html.escape(item["name"])}</h2><p>ID：{html.escape(item["external_id"])}</p><small>{"真实头像 · " if item["avatar"] else "头像待取得 · "}权利待审核</small></div></article>''')
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>演员头像数据预览</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f1ea;color:#1c1a17;font-family:system-ui,"Microsoft YaHei",sans-serif}}header{{padding:36px max(24px,5vw);background:#1d1c18;color:#fff}}header small{{color:#ff8b62;font-weight:800;letter-spacing:.14em}}h1{{font-size:clamp(32px,5vw,60px);margin:12px 0}}header p{{color:#c9c3b9;line-height:1.7}}.stats{{display:flex;gap:10px;margin-top:18px}}.stats b{{background:#302e29;border-radius:999px;padding:8px 12px}}main{{padding:30px max(24px,5vw) 70px}}input{{width:min(520px,100%);padding:13px 15px;border:1px solid #d9d2c7;border-radius:12px;font:inherit;margin-bottom:22px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:17px}}.card{{background:#fff;border:1px solid #ded8cc;border-radius:17px;overflow:hidden}}.card>img,.placeholder{{display:grid;place-items:center;width:100%;aspect-ratio:1/1;object-fit:cover;background:#e9e3d9;font-size:44px;color:#8a8175}}.body{{padding:16px}}.body span{{color:#c4542e;font-size:11px}}h2{{margin:7px 0 10px;font-size:18px}}p{{color:#756f64;font-size:12px}}small{{color:#8a641b}}footer{{padding:22px max(24px,5vw);border-top:1px solid #ded8cc;color:#756f64;font-size:12px}}
</style></head><body><header><small>JAVDB / PERFORMER MEDIA STAGING</small><h1>真实演员头像预览</h1><p>头像来自公开演员列表页并已保存到本地私有 staging；页面不请求外部资源，审核完成前不作为正式公开媒体发布。</p><div class="stats"><b>演员 {len(performers)}</b><b>本地头像 {dataset["counts"]["avatars"]}</b></div></header><main><input id="search" type="search" placeholder="搜索演员姓名或来源 ID"><section class="grid">{"".join(cards)}</section></main><footer>rights_status=needs_review · publication_status=staging</footer><script>const input=document.querySelector('#search');const cards=[...document.querySelectorAll('.card')];input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();for(const card of cards)card.hidden=q&&!card.dataset.search.includes(q)}});</script></body></html>'''
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    report = {"output_dir": str(output_dir), **dataset["counts"], "external_requests": 0}
    _write_json(output_dir / "showcase-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest, download and preview public performer avatars")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser("ingest-javdb-actors")
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--source-url", required=True)
    ingest.add_argument("--checked-at", required=True)
    ingest.add_argument("--database", type=Path, required=True)
    ingest.add_argument("--output-dir", type=Path, required=True)
    download = subparsers.add_parser("download-javdb-avatars")
    download.add_argument("--database", type=Path, required=True)
    download.add_argument("--output-dir", type=Path, required=True)
    download.add_argument("--max-images", type=int, default=40)
    download.add_argument("--acknowledgement", required=True)
    showcase = subparsers.add_parser("build-actor-showcase")
    showcase.add_argument("--database", type=Path, required=True)
    showcase.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest-javdb-actors":
            result = ingest_javdb_actor_snapshot(
                args.input, source_url=args.source_url, checked_at=args.checked_at,
                database=args.database, output_dir=args.output_dir,
            )
        elif args.command == "download-javdb-avatars":
            result = download_javdb_avatars(
                args.database, args.output_dir, max_images=args.max_images,
                acknowledgement=args.acknowledgement,
            )
        else:
            result = build_actor_showcase(args.database, args.output_dir)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "PERFORMER_ASSET_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
