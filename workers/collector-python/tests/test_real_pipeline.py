from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.javdb import CONNECTOR_VERSION  # noqa: E402
from collector.live import _load_scope  # noqa: E402
from collector.media import inspect_image  # noqa: E402
from collector.pipeline import export_selfdeepsearch, ingest_samples  # noqa: E402


HTML = """<!doctype html><html><head><title>DEMO-001 Safe test | JavDB</title></head><body>
<div class="video-detail"><h2><strong class="current-title">Safe test</strong></h2>
<div class="column-video-cover"><img class="video-cover" src="https://c0.jdbstatic.com/covers/sa/sample1.jpg"></div>
<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_0.jpg"></a>
<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_1.jpg"></a>
<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_2.jpg"></a>
<a class="tile-item" href="https://c0.jdbstatic.com/samples/sa/sample1_l_3.jpg"></a>
<nav class="movie-panel-info">
<div class="panel-block"><strong>番號:</strong><span class="value">DEMO-001</span></div>
<div class="panel-block"><strong>日期:</strong><span class="value">2026-09-01</span></div>
<div class="panel-block"><strong>片商:</strong><span class="value">Studio</span></div>
<div class="panel-block"><strong>演員:</strong><span class="value"><a href="/actors/a">Person</a></span></div>
</nav></div></body></html>"""


class RealPipelineTests(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        (root / "sample.html").write_text(HTML, encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "source_id": "javdb_reference",
            "connector_version": CONNECTOR_VERSION,
            "checked_at": "2026-09-16T00:00:00Z",
            "samples": [{"file": "sample.html", "url": "https://javdb.com/v/sample1"}],
        }), encoding="utf-8")
        return manifest

    def test_sqlite_ingest_is_idempotent_and_exports_staging_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "collector.db"
            manifest = self._manifest(root)
            first = ingest_samples(manifest, database, root / "first")
            second = ingest_samples(manifest, database, root / "second")
            exported = export_selfdeepsearch(database, root / "export")
            staging = [json.loads(line) for line in (root / "export" / "media_staging.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(first["database_stats"]["works"], 1)
        self.assertEqual(second["database_stats"]["collection_runs"], 2)
        self.assertEqual(second["database_stats"]["work_observations"], 1)
        self.assertEqual(second["database_stats"]["media_candidates"], 5)
        self.assertEqual(exported["display_eligible_media"], 4)
        self.assertEqual(len(staging), 5)
        self.assertEqual([item["position"] for item in staging], [0, 1, 2, 3, None])
        self.assertTrue(all(item["rights_status"] == "needs_review" for item in staging))

    def test_image_inspection_reads_png_dimensions_and_rejects_unknown_input(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 320, 200)
        self.assertEqual(inspect_image(png), ("image/png", ".png", 320, 200))
        with self.assertRaises(ValueError):
            inspect_image(b"not an image")

    def test_live_scope_requires_robots_delay_and_bounded_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            robots = root / "robots.txt"
            robots.write_text("User-agent: *\nCrawl-delay: 20\n", encoding="utf-8")
            scope = root / "scope.json"
            scope.write_text(json.dumps({
                "source_id": "javdb_reference", "connector_version": CONNECTOR_VERSION,
                "network_access": True, "acknowledgement": "public_metadata_review_only",
                "minimum_interval_seconds": 19, "response_bytes": 1024, "timeout_seconds": 10,
                "urls": ["https://javdb.com/v/sample1"],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least 20"):
                _load_scope(scope, robots)


if __name__ == "__main__":
    unittest.main()
