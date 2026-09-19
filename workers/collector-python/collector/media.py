from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from collector.storage import connect, now_iso

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_MEDIA_HOST = "c0.jdbstatic.com"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    offset = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in sof and length >= 7:
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return width, height
        offset += length
    raise ValueError("JPEG dimensions are missing")


def inspect_image(data: bytes) -> tuple[str, str, int, int]:
    if data.startswith(b"\xff\xd8\xff"):
        width, height = _jpeg_dimensions(data)
        return "image/jpeg", ".jpg", width, height
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", ".png", width, height
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30 and data[12:16] == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return "image/webp", ".webp", width, height
    raise ValueError("only decoded-looking JPEG, PNG or extended WebP input is accepted")


def download_media(
    database: Path,
    output_dir: Path,
    *,
    max_images: int,
    acknowledgement: str,
    purpose: str = "cover",
) -> dict[str, object]:
    if acknowledgement != "internal_review_only":
        raise ValueError("acknowledgement must be internal_review_only")
    if not 1 <= max_images <= 20:
        raise ValueError("max_images must be between 1 and 20")
    if purpose not in {"cover", "display"}:
        raise ValueError("purpose must be cover or display")
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = connect(database)
    purpose_filter = "purpose = 'cover'" if purpose == "cover" else "purpose IN ('cover', 'gallery')"
    rows = connection.execute(
        f"""
        SELECT media_candidate_id, candidate_url FROM media_candidates
        WHERE {purpose_filter} AND display_eligible = 1 AND download_status = 'pending'
        ORDER BY checked_at, external_id, display_position LIMIT ?
        """,
        (max_images,),
    ).fetchall()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(), NoRedirect())
    downloaded = []
    errors = []
    try:
        for index, row in enumerate(rows):
            if index:
                time.sleep(2)
            url = row["candidate_url"]
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != ALLOWED_MEDIA_HOST or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("media URL is outside the approved host")
            request = urllib.request.Request(url, headers={"User-Agent": "self-deepsearch-collector/0.4 internal-review"})
            try:
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
                    UPDATE media_candidates SET download_status='downloaded', local_path=?, sha256=?, mime_type=?,
                      width=?, height=?, byte_size=?, updated_at=? WHERE media_candidate_id=?
                    """,
                    (str(destination.resolve()), digest, mime_type, width, height, len(raw), now_iso(), row["media_candidate_id"]),
                )
                connection.commit()
                downloaded.append({"media_candidate_id": row["media_candidate_id"], "path": str(destination), "sha256": digest, "mime_type": mime_type, "width": width, "height": height, "byte_size": len(raw)})
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
                connection.execute("UPDATE media_candidates SET download_status='failed', updated_at=? WHERE media_candidate_id=?", (now_iso(), row["media_candidate_id"]))
                connection.commit()
                errors.append({"media_candidate_id": row["media_candidate_id"], "message": str(exc)})
        database_downloaded_total = connection.execute(
            "SELECT COUNT(*) FROM media_candidates WHERE download_status = 'downloaded'"
        ).fetchone()[0]
    finally:
        connection.close()
    staged_file_total = sum(
        path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        for path in output_dir.iterdir()
    )
    report = {
        "requested_this_run": len(rows),
        "downloaded_this_run": len(downloaded),
        "failed_this_run": len(errors),
        "database_downloaded_total": database_downloaded_total,
        "staged_file_total": staged_file_total,
        "purpose": purpose,
        "staging_only": True,
        "rights_status": "needs_review",
        "items_this_run": downloaded,
        "errors_this_run": errors,
    }
    (output_dir / "download-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a bounded set of cover candidates into private staging")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-images", type=int, default=2)
    parser.add_argument("--purpose", choices=("cover", "display"), default="cover")
    parser.add_argument("--acknowledgement", required=True)
    args = parser.parse_args(argv)
    try:
        report = download_media(
            args.database,
            args.output_dir,
            max_images=args.max_images,
            acknowledgement=args.acknowledgement,
            purpose=args.purpose,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"level": "error", "code": "MEDIA_DOWNLOAD_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
