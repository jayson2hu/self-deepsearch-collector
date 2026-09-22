from __future__ import annotations

import json
import csv
import importlib.util
import struct
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.jable import CONNECTOR_VERSION, parse_jable_html  # noqa: E402
from collector.jable_assets import archive_jable, audit_jable, audit_jable_coverage, build_performer_showcase, download_jable_media, ingest_jable_candidates, ingest_jable_manifest, load_current_performer_payloads, parse_jable_manifest  # noqa: E402
from collector.storage import connect  # noqa: E402
from collector.pipeline import export_selfdeepsearch  # noqa: E402


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

    def test_profile_keeps_requested_actor_and_explicit_fields_only(self) -> None:
        sample = '''<meta property="og:title" content="Jable.TV - 最新高清影片">
          <meta name="description" content="generic site advertising">
          <section class="related-models"><h1>推荐演员</h1>
            <a href="/models/another/"><h6>另一演员</h6>
              <img src="https://assets-cdn.jable.tv/contents/models/2/another.jpg"></a></section>
          <main data-performer-profile><h1>示例姓名</h1>
            <span data-performer-field="aliases">别名一、別名二</span>
            <p data-performer-field="biography">公开的演员简介。</p>
            <time itemprop="birthDate" datetime="1990-01-02">1990 年 1 月 2 日</time>
            <span itemprop="height">160 cm</span>
            <img class="avatar" src="https://assets-cdn.jable.tv/contents/models/1/person.jpg">
            <a href="/videos/example/"><h1>影片标题</h1>
              <img src="https://assets-cdn.jable.tv/contents/videos/example.jpg"></a>
          </main>'''
        candidates = parse_jable_html(sample, source_url="https://jable.tv/models/example/", checked_at="2026-09-22T00:00:00Z")
        self.assertEqual([item["external_id"] for item in candidates], ["example"])
        self.assertEqual(candidates[0]["payload"], {
            "profile_url": "https://jable.tv/models/example/", "name": "示例姓名",
            "aliases": ["別名二", "别名一"], "biography": "公开的演员简介。",
            "birth_date": "1990-01-02", "height": "160 cm",
        })
        self.assertEqual([item["candidate_url"] for item in candidates[0]["media"]], ["https://assets-cdn.jable.tv/contents/models/1/person.jpg"])

    def test_profile_never_invents_name_from_slug_or_marketing_titles(self) -> None:
        for sample in (
            '<title>Jable.TV - Free HD Videos</title><meta property="og:title" content="Jable.TV - 最新高清影片">',
            '<title>示例姓名 - Jable.TV</title>',
            '<section class="recommended"><h1>另一演员</h1></section>',
            '<section itemscope itemtype="https://schema.org/VideoObject"><h1>影片专有标题</h1></section>',
        ):
            with self.subTest(sample=sample), self.assertRaisesRegex(ValueError, "no observed performer fields"):
                parse_jable_html(sample, source_url="https://jable.tv/models/not-an-observed-name/", checked_at="2026-09-22T00:00:00Z")

    def test_portrait_paths_reject_video_files_and_encoded_traversal(self) -> None:
        for path in ("/contents/models/1/movie.mp4", "/contents/models/%2e%2e/videos/cover.jpg", "/contents/models/1/../../videos/cover.jpg"):
            sample = f'<h1>示例姓名</h1><img class="avatar" src="https://assets-cdn.jable.tv{path}">'
            result = parse_jable_html(sample, source_url="https://jable.tv/models/example/", checked_at="2026-09-22T00:00:00Z")
            self.assertEqual(result[0]["media"], [])

    def test_detail_enrichment_preserves_listing_fact_and_its_original_provenance(self) -> None:
        listing_time = "2026-09-19T00:00:00Z"
        detail_time = "2026-09-22T00:00:00Z"
        listing = parse_jable_html('<a href="/models/example/"><h6>原名</h6><span>123 部影片</span></a>', source_url="https://jable.tv/models/2/", checked_at=listing_time)
        detail = parse_jable_html('<h1>现名</h1><p data-performer-field="biography">公开简介。</p>', source_url="https://jable.tv/models/example/", checked_at=detail_time)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collector.db"
            ingest_jable_candidates(listing, database, checked_at=listing_time)
            ingest_jable_candidates(detail, database, checked_at=detail_time)
            # Reimporting an older capture must not move the current name backwards.
            ingest_jable_candidates(listing, database, checked_at=listing_time)
            connection = connect(database)
            try:
                current = load_current_performer_payloads(connection)["example"]
                self.assertEqual(current["payload"]["name"], "现名")
                self.assertEqual(current["payload"]["work_count"], 123)
                self.assertEqual(current["field_provenance"]["work_count"]["checked_at"], listing_time)
                self.assertEqual(current["field_provenance"]["work_count"]["source_url"], "https://jable.tv/models/2/")
                self.assertEqual(current["field_provenance"]["biography"]["checked_at"], detail_time)
                performer = connection.execute("SELECT * FROM performers").fetchone()
                self.assertEqual(performer["name"], "现名")
                self.assertEqual(performer["current_content_hash"], detail[0]["content_hash"])
                observations = [json.loads(row[0]) for row in connection.execute("SELECT candidate_json FROM performer_observations")]
                self.assertEqual(len(observations), 2)
                self.assertIn(detail[0], observations)
                self.assertNotIn("work_count", detail[0]["payload"])
            finally:
                connection.close()
            build_performer_showcase(database, Path(directory) / "showcase")
            catalog = json.loads((Path(directory) / "showcase" / "performers.json").read_text())
            self.assertEqual(catalog["performers"][0]["work_count"], 123)
            self.assertEqual(catalog["performers"][0]["biography"], "公开简介。")
            export_selfdeepsearch(database, Path(directory) / "export")
            exported = json.loads((Path(directory) / "export" / "performers.jsonl").read_text())
            self.assertEqual(exported["work_count"], 123)
            self.assertEqual(exported["biography"], "公开简介。")
            self.assertEqual(exported["field_provenance"]["work_count"]["checked_at"], listing_time)
            self.assertEqual(exported["current_content_hash"], detail[0]["content_hash"])

    def test_prepare_exports_partial_enrichment_and_parseable_csv_provenance(self) -> None:
        script = Path(__file__).resolve().parents[3] / "scripts" / "prepare_primary_data.py"
        spec = importlib.util.spec_from_file_location("prepare_primary_data_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        listing_time = "2026-09-19T00:00:00Z"
        detail_time = "2026-09-22T00:00:00Z"
        listing = parse_jable_html('''<a href="/models/first/"><h6>A Person</h6></a>
            <a href="/models/enriched/"><h6>Z Person</h6><span>12 部影片</span></a>''',
            source_url="https://jable.tv/models/", checked_at=listing_time)
        detail = parse_jable_html('''<h1>Z Person</h1>
            <span data-performer-field="aliases">Z Alias</span>
            <p data-performer-field="biography">公开简介。</p>
            <span data-performer-field="height">160 cm</span>''',
            source_url="https://jable.tv/models/enriched/", checked_at=detail_time)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "collector.db"
            ingest_jable_candidates(listing, database, checked_at=listing_time)
            ingest_jable_candidates(detail, database, checked_at=detail_time)
            archive_jable(database, root / "archive")
            result = module.prepare(root / "archive", root / "prepared")
            profiles = [json.loads(line) for line in (root / "prepared" / "profiles" / "performers.jsonl").read_text().splitlines()]
            enriched = next(row for row in profiles if row["external_id"] == "enriched")
            self.assertEqual(result["performers"], 2)
            self.assertEqual(enriched["work_count"], 12)
            self.assertEqual(enriched["aliases"], ["Z Alias"])
            self.assertEqual(enriched["height"], "160 cm")
            self.assertEqual(enriched["current_content_hash"], detail[0]["content_hash"])
            self.assertEqual(enriched["field_provenance"]["work_count"]["checked_at"], listing_time)
            self.assertEqual(enriched["field_provenance"]["aliases"]["checked_at"], detail_time)
            with (root / "prepared" / "profiles" / "performers.csv").open(encoding="utf-8-sig", newline="") as stream:
                csv_profiles = list(csv.DictReader(stream))
            csv_enriched = next(row for row in csv_profiles if row["external_id"] == "enriched")
            self.assertEqual(json.loads(csv_enriched["aliases"]), ["Z Alias"])
            self.assertEqual(json.loads(csv_enriched["field_provenance"]), enriched["field_provenance"])
            self.assertEqual(csv_enriched["biography"], "公开简介。")

    def test_manifest_keeps_page_observations_separate_with_sample_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "listing.html").write_text('<a href="/models/example/"><h6>示例姓名</h6><span>0 部影片</span></a>')
            (root / "profile.html").write_text('<h1>示例姓名</h1><span data-performer-field="aliases">别名</span>')
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"source_id": "jable_reference", "connector_version": CONNECTOR_VERSION,
                "checked_at": "2026-09-22T00:00:00Z", "samples": [
                    {"file": "listing.html", "url": "https://jable.tv/models/", "checked_at": "2026-09-19T00:00:00Z"},
                    {"file": "profile.html", "url": "https://jable.tv/models/example/"},
                ]}))
            result = ingest_jable_manifest(manifest, root / "collector.db", root / "parsed")
            self.assertEqual(result["performers"], 1)
            self.assertEqual(result["database_counts"]["observations"], 2)
            observations = [json.loads(line) for line in (root / "parsed" / "performers.jsonl").read_text().splitlines()]
            self.assertEqual([row["provenance"]["checked_at"] for row in observations], ["2026-09-19T00:00:00Z", "2026-09-22T00:00:00Z"])
            self.assertNotIn("aliases", observations[0]["payload"])
            self.assertNotIn("work_count", observations[1]["payload"])
            connection = connect(root / "collector.db")
            try:
                self.assertEqual(load_current_performer_payloads(connection)["example"]["payload"]["work_count"], 0)
            finally:
                connection.close()

    def test_partial_profile_keeps_existing_name_without_fabricating_raw_name(self) -> None:
        observed_at = "2026-09-22T00:00:00Z"
        partial = parse_jable_html('<span data-performer-field="aliases">公开别名</span>', source_url="https://jable.tv/models/aoba-haru/", checked_at=observed_at)
        self.assertNotIn("name", partial[0]["payload"])
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collector.db"
            with self.assertRaisesRegex(ValueError, "no observed name"):
                ingest_jable_candidates(partial, database, checked_at=observed_at)
            connection = connect(database)
            try:
                self.assertEqual(connection.execute("SELECT status FROM collection_runs").fetchone()[0], "failed")
            finally:
                connection.close()
            listing = parse_jable_html(LISTING_HTML, source_url="https://jable.tv/models/", checked_at="2026-09-19T00:00:00Z")
            ingest_jable_candidates(listing, database, checked_at=observed_at)
            ingest_jable_candidates(partial, database, checked_at=observed_at)
            connection = connect(database)
            try:
                current = load_current_performer_payloads(connection)["aoba-haru"]
                self.assertEqual(current["payload"]["name"], "青葉はる")
                self.assertEqual(current["payload"]["aliases"], ["公开别名"])
            finally:
                connection.close()

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
