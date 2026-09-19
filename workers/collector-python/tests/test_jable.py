from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.jable import CONNECTOR_VERSION, parse_jable_html  # noqa: E402
from collector.jable_assets import archive_jable, audit_jable, audit_jable_coverage, build_performer_showcase, download_jable_media, ingest_jable_manifest, parse_jable_manifest  # noqa: E402
from collector.storage import connect  # noqa: E402


LISTING_HTML = """<!doctype html><html><head><title>Models - Jable.TV</title></head><body>
<a class="model-card" href="/models/aoba-haru/"><img data-src="https://assets-cdn.jable.tv/models/aoba.jpg" alt="青葉はる"></a>
<a class="model-card" href="https://jable.tv/models/kuraki-shiori/"><img src="https://assets-cdn.jable.tv/models/shiori.jpg" alt="倉木しおり"></a>
</body></html>"""

PROFILE_HTML = """<!doctype html><html><head>
<title>青葉はる - Jable.TV</title><meta property="og:title" content="青葉はる - Jable.TV">
<meta property="og:image" content="https://assets-cdn.jable.tv/models/aoba.jpg"></head><body>
<h1>青葉はる</h1>
<a href="/videos/demo-1/"><img data-src="https://assets-cdn.jable.tv/contents/demo-1.jpg" alt="作品一"></a>
<a href="/videos/demo-2/"><img data-src="https://assets-cdn.jable.tv/contents/demo-2.jpg" alt="作品二"></a>
</body></html>"""


class JableConnectorTests(unittest.TestCase):
    def test_listing_and_profile_extract_names_and_portraits_only(self) -> None:
        listing = parse_jable_html(
            LISTING_HTML, source_url="https://jable.tv/models/", checked_at="2026-09-17T00:00:00Z",
        )
        profile = parse_jable_html(
            PROFILE_HTML, source_url="https://jable.tv/models/aoba-haru/", checked_at="2026-09-17T00:00:00Z",
        )
        self.assertEqual([item["payload"]["name"] for item in listing], ["青葉はる", "倉木しおり"])
        self.assertEqual(listing[0]["media"][0]["purpose"], "avatar")
        self.assertEqual([item["purpose"] for item in profile[0]["media"]], ["avatar"])

    def test_real_card_layout_ignores_placeholder_letters_counts_and_pagination(self) -> None:
        sample = '''<a href="/models/example-name/"><div class="text-avatar">名</div>
          <h6 class="title">示例姓名</h6><span>123 部影片</span></a>
          <a href="/models/2/">02</a><a href="https://invalid.example/models/injected/">external</a>'''
        result = parse_jable_html(sample, source_url="https://jable.tv/models/2/", checked_at="2026-09-19T00:00:00Z")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["payload"]["name"], "示例姓名")
        self.assertEqual(result[0]["payload"]["work_count"], 123)
        self.assertEqual(result[0]["media"], [])

    def test_content_hash_is_stable_across_capture_times(self) -> None:
        first = parse_jable_html(LISTING_HTML, source_url="https://jable.tv/models/", checked_at="2026-09-18T00:00:00Z")
        second = parse_jable_html(LISTING_HTML, source_url="https://jable.tv/models/", checked_at="2026-09-19T00:00:00Z")
        self.assertEqual([row["content_hash"] for row in first], [row["content_hash"] for row in second])

    def test_generic_profile_og_image_is_not_a_portrait(self) -> None:
        sample = '<h1>示例姓名</h1><meta property="og:image" content="https://assets-cdn.jable.tv/contents/videos/example.jpg">'
        result = parse_jable_html(sample, source_url="https://jable.tv/models/example/", checked_at="2026-09-19T00:00:00Z")
        self.assertEqual(result[0]["media"], [])

    def test_media_stops_on_access_denial_and_does_not_retry_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models.html").write_text(LISTING_HTML.replace('/models/aoba.jpg', '/contents/models/1/aoba.jpg').replace('/models/shiori.jpg', '/contents/models/2/shiori.jpg'), encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"source_id":"jable_reference", "connector_version":CONNECTOR_VERSION,
                "checked_at":"2026-09-19T00:00:00Z", "samples":[{"file":"models.html", "url":"https://jable.tv/models/"}]}), encoding="utf-8")
            ingest_jable_manifest(manifest, root / "collector.db", root / "parsed")
            with patch('collector.jable_assets.urllib.request.build_opener') as mocked:
                mocked.return_value.open.side_effect = urllib.error.HTTPError('https://assets-cdn.jable.tv/contents/models/1/a.jpg', 403, 'Forbidden', {}, None)
                result = download_jable_media(root / "collector.db", root / "media", max_images=20, acknowledgement="internal_review_only")
                self.assertEqual(mocked.return_value.open.call_count, 1)
            self.assertEqual(result["attempted_this_run"], 1)
            self.assertEqual(result["unattempted_this_run"], 1)
            self.assertTrue(result["halted_on_access"])
            with patch('collector.jable_assets.urllib.request.build_opener') as mocked:
                mocked.return_value.open.side_effect = urllib.error.HTTPError('https://assets-cdn.jable.tv/contents/models/2/b.jpg', 403, 'Forbidden', {}, None)
                download_jable_media(root / "collector.db", root / "media", max_images=20, acknowledgement="internal_review_only", retry_transient=True)
                self.assertEqual(mocked.return_value.open.call_count, 1)
            with patch('collector.jable_assets.urllib.request.build_opener') as mocked:
                result = download_jable_media(root / "collector.db", root / "media", max_images=20, acknowledgement="internal_review_only", retry_transient=True)
                mocked.return_value.open.assert_not_called()
            self.assertEqual(result["requested_this_run"], 0)

    def test_coverage_recounts_resume_chain_and_detects_tampered_evidence(self) -> None:
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = []
            for number in (1, 2):
                batch = root / f"batch-{number}"
                page_dir = batch / f"page-{number}"
                page_dir.mkdir(parents=True)
                (page_dir / "models.html").write_text(LISTING_HTML, encoding="utf-8")
                report = {"source_id":"jable_reference", "discovered_last_page":2, "pages":[{
                    "page":number, "status":"captured", "directory":str(page_dir),
                    "projection_sha256":hashlib.sha256((page_dir / "models.html").read_bytes()).hexdigest(),
                    "source_url":"https://jable.tv/models/" if number==1 else "https://jable.tv/models/2/",
                    "checked_at":"2026-09-19T00:00:00Z"}]}
                if reports:
                    report["resumed_from"] = str(reports[-1])
                report_path = batch / "crawl-report.json"
                report_path.write_text(json.dumps(report), encoding="utf-8")
                reports.append(report_path)
            audit = audit_jable_coverage(reports[-1], root / "audit")
            self.assertTrue(audit["listing_pages_complete"])
            self.assertFalse(audit["full_site_coverage"])
            self.assertEqual(audit["unique_performers_in_saved_pages"], 2)
            self.assertEqual(audit["duplicate_occurrences"], 2)
            (root / "batch-1" / "page-1" / "models.html").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                audit_jable_coverage(reports[-1], root / "audit")

    def test_cloudflare_page_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Cloudflare challenge"):
            parse_jable_html(
                "<title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/cf-chl-test'></script>",
                source_url="https://jable.tv/models/", checked_at="2026-09-17T00:00:00Z",
            )

    def test_manifest_ingest_is_idempotent_and_showcase_uses_local_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "listing.html").write_text(LISTING_HTML, encoding="utf-8")
            (root / "profile.html").write_text(PROFILE_HTML, encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_id": "jable_reference",
                "connector_version": CONNECTOR_VERSION,
                "checked_at": "2026-09-17T00:00:00Z",
                "samples": [
                    {"file": "listing.html", "url": "https://jable.tv/models/"},
                    {"file": "profile.html", "url": "https://jable.tv/models/aoba-haru/"},
                ],
            }), encoding="utf-8")
            parsed = parse_jable_manifest(manifest, root / "parsed")
            first = ingest_jable_manifest(manifest, root / "collector.db", root / "first")
            second = ingest_jable_manifest(manifest, root / "collector.db", root / "second")
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 320, 320)
            image_path = root / "avatar.png"
            image_path.write_bytes(png)
            connection = connect(root / "collector.db")
            try:
                row = connection.execute(
                    "SELECT media_candidate_id FROM performer_media_candidates WHERE purpose='avatar' ORDER BY external_id LIMIT 1"
                ).fetchone()
                connection.execute(
                    """
                    UPDATE performer_media_candidates SET download_status='downloaded', local_path=?,
                      sha256=?, mime_type='image/png', width=320, height=320, byte_size=?
                    WHERE media_candidate_id=?
                    """,
                    (str(image_path), "0" * 64, len(png), row["media_candidate_id"]),
                )
                connection.commit()
            finally:
                connection.close()
            showcase = build_performer_showcase(root / "collector.db", root / "showcase")
            catalog = json.loads((root / "showcase" / "performers.json").read_text(encoding="utf-8"))
            # Audit must identify this intentionally incorrect fixture checksum.
            audit = audit_jable(root / "collector.db", root / "audit")
            self.assertEqual(len(audit["corrupt_or_missing_files"]), 1)
            import hashlib
            connection = connect(root / "collector.db")
            connection.execute("UPDATE performer_media_candidates SET sha256=? WHERE local_path=?", (hashlib.sha256(png).hexdigest(), str(image_path)))
            connection.commit()
            connection.close()
            archive = archive_jable(root / "collector.db", root / "archive")
            self.assertEqual(archive["copied_avatars"], 1)
            self.assertTrue(archive["portable_relative_media_paths"])
            self.assertEqual(audit_jable(root / "archive" / "collector.db", root / "archive-audit")["downloaded_avatars"], 1)
            self.assertEqual(build_performer_showcase(root / "archive" / "collector.db", root / "archive-showcase")["avatars"], 1)
        self.assertEqual(parsed["performers"], 2)
        self.assertEqual(first["database_counts"]["performers"], 2)
        self.assertEqual(second["database_counts"]["observations"], 2)
        self.assertEqual(showcase["performers"], 2)
        self.assertEqual(showcase["avatars"], 1)
        self.assertEqual(catalog["counts"]["avatars"], 1)


if __name__ == "__main__":
    unittest.main()
