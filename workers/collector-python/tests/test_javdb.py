from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.javdb import CONNECTOR_VERSION, ParseError, extract_media_candidates, parse_detail  # noqa: E402
from collector.contracts import ContractError  # noqa: E402
from collector.samples import parse_manifest  # noqa: E402


VALID_HTML = """<!doctype html><html><head><title> DEMO-001 Synthetic title | JavDB</title></head><body>
<div class="video-detail"><h2 class="title is-4"><strong>DEMO-001</strong>
<strong class="current-title">Synthetic &amp; safe title</strong></h2>
<nav class="panel movie-panel-info">
<div class="panel-block first-block"><strong>番號:</strong><span class="value"><a>DEMO</a>-001</span></div>
<div class="panel-block"><strong>日期:</strong><span class="value">2026-09-01</span></div>
<div class="panel-block"><strong>片商:</strong><span class="value"><a>Example Studio</a></span></div>
<div class="panel-block"><strong>演員:</strong><span class="value"><a href="/actors/a1">Example A</a>, <a href="/actors/a2">Example B</a></span></div>
</nav></div></body></html>"""


class JavdbParserTests(unittest.TestCase):
    def test_extracts_supported_fields_and_evidence(self) -> None:
        candidate = parse_detail(VALID_HTML, source_url="https://javdb.com/v/sample1", checked_at="2026-09-16T14:30:01Z")
        self.assertEqual(candidate["external_id"], "sample1")
        self.assertEqual(candidate["payload"], {
            "canonical_code": "DEMO-001",
            "title": "Synthetic & safe title",
            "release_date": "2026-09-01",
            "studio_name": "Example Studio",
            "performer_aliases": ["Example A", "Example B"],
        })
        self.assertEqual(candidate["provenance"]["connector_version"], CONNECTOR_VERSION)
        self.assertIn("field_evidence", candidate["provenance"])

    def test_optional_fields_remain_empty(self) -> None:
        html = VALID_HTML.replace('<div class="panel-block"><strong>日期:</strong><span class="value">2026-09-01</span></div>', "")
        html = html.replace('<div class="panel-block"><strong>片商:</strong><span class="value"><a>Example Studio</a></span></div>', "")
        html = html.replace('<div class="panel-block"><strong>演員:</strong><span class="value"><a href="/actors/a1">Example A</a>, <a href="/actors/a2">Example B</a></span></div>', "")
        payload = parse_detail(html, source_url="https://javdb.com/v/sample2", checked_at="2026-09-16T00:00:00Z")["payload"]
        self.assertIsNone(payload["release_date"])
        self.assertIsNone(payload["studio_name"])
        self.assertEqual(payload["performer_aliases"], [])

    def test_challenge_page_is_rejected(self) -> None:
        with self.assertRaisesRegex(ParseError, "video-detail"):
            parse_detail("<html><title>Just a moment</title></html>", source_url="https://javdb.com/v/sample3", checked_at="now")

    def test_noncanonical_or_authenticated_url_is_rejected(self) -> None:
        for url in ("http://javdb.com/v/a", "https://user:pass@javdb.com/v/a", "https://example.com/v/a", "https://javdb.com/search?q=a"):
            with self.subTest(url=url), self.assertRaises(ParseError):
                parse_detail(VALID_HTML, source_url=url, checked_at="now")

    def test_invalid_date_is_rejected(self) -> None:
        html = VALID_HTML.replace("2026-09-01", "2026-02-30")
        with self.assertRaisesRegex(ParseError, "calendar date"):
            parse_detail(html, source_url="https://javdb.com/v/sample4", checked_at="now")

    def test_dotted_western_code_is_normalized_without_losing_raw_evidence(self) -> None:
        html = VALID_HTML.replace('<a>DEMO</a>-001', "Blackedraw.2026.09.13")
        candidate = parse_detail(html, source_url="https://javdb.com/v/western1", checked_at="now")
        self.assertEqual(candidate["payload"]["canonical_code"], "BLACKEDRAW-2026-09-13")
        self.assertEqual(candidate["provenance"]["field_evidence"]["canonical_code"]["raw"], "Blackedraw.2026.09.13")

    def test_media_candidates_keep_all_source_images_but_only_three_gallery_slots(self) -> None:
        html = VALID_HTML.replace(
            '<nav class="panel movie-panel-info">',
            '<div class="column-video-cover"><img class="video-cover" src="https://c0.jdbstatic.com/covers/sa/sample1.jpg"></div>'
            '<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_0.jpg"></a>'
            '<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_1.jpg"></a>'
            '<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_2.jpg"></a>'
            '<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_3.jpg"></a>'
            '<nav class="panel movie-panel-info">',
        )
        media = extract_media_candidates(html, source_url="https://javdb.com/v/sample1", checked_at="now")
        self.assertEqual(len(media), 5)
        self.assertEqual(sum(item["display_eligible"] for item in media), 4)
        self.assertEqual([item["display_position"] for item in media], [0, 1, 2, 3, None])

    def test_manifest_writes_candidates_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.html").write_text(VALID_HTML, encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_id": "javdb_reference",
                "connector_version": CONNECTOR_VERSION,
                "checked_at": "2026-09-16T14:30:01Z",
                "samples": [{"file": "sample.html", "url": "https://javdb.com/v/sample1"}],
            }), encoding="utf-8")
            report = parse_manifest(manifest, root / "output")
            candidates = (root / "output" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(report["valid_candidates"], 1)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(report["network_access"])

    def test_manifest_rejects_wrong_connector_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_id": "javdb_reference", "connector_version": "old", "checked_at": "now",
                "samples": [{"file": "sample.html", "url": "https://javdb.com/v/sample1"}],
            }), encoding="utf-8")
            with self.assertRaises(ContractError):
                parse_manifest(manifest, root / "output")

    def test_manifest_rejects_duplicate_source_objects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.html").write_text(VALID_HTML, encoding="utf-8")
            (root / "two.html").write_text(VALID_HTML, encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "source_id": "javdb_reference", "connector_version": CONNECTOR_VERSION, "checked_at": "now",
                "samples": [
                    {"file": "one.html", "url": "https://javdb.com/v/same"},
                    {"file": "two.html", "url": "https://javdb.com/v/same"},
                ],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "duplicate external_id"):
                parse_manifest(manifest, root / "output")


if __name__ == "__main__":
    unittest.main()
