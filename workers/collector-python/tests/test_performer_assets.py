from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.javdb_actors import parse_javdb_actor_list  # noqa: E402
from collector.performer_assets import build_actor_showcase, ingest_javdb_actor_snapshot  # noqa: E402
from collector.pipeline import export_selfdeepsearch  # noqa: E402
from collector.storage import connect  # noqa: E402


HTML = """<!doctype html><html><body><div id="actors">
<div class="box actor-box"><a href="/actors/AAA1" title="演员甲, 别名甲"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg"></figure></a></div>
<div class="box actor-box"><a href="/actors/BBB2" title="演员乙"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/bb/BBB2.jpg"></figure></a></div>
<div class="box actor-box"><a href="/actors/AAA1" title="演员甲, 别名甲"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg"></figure></a></div>
</div></body></html>"""


class PerformerAssetTests(unittest.TestCase):
    def test_actor_listing_extracts_unique_real_avatar_candidates(self) -> None:
        candidates = parse_javdb_actor_list(
            HTML, source_url="https://javdb.com/actors", checked_at="2026-09-18T00:00:00Z",
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["payload"]["aliases"], ["演员甲", "别名甲"])
        self.assertEqual(candidates[0]["media"][0]["purpose"], "avatar")

    def test_actor_listing_rejects_challenge(self) -> None:
        with self.assertRaisesRegex(ValueError, "challenge"):
            parse_javdb_actor_list(
                "<title>Just a moment...</title><div>captcha</div>",
                source_url="https://javdb.com/actors", checked_at="2026-09-18T00:00:00Z",
            )

    def test_ingest_and_showcase_use_local_avatar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "actors.html"
            source.write_text(HTML, encoding="utf-8")
            database = root / "collector.db"
            result = ingest_javdb_actor_snapshot(
                source, source_url="https://javdb.com/actors", checked_at="2026-09-18T00:00:00Z",
                database=database, output_dir=root / "ingested",
            )
            png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 256, 256)
            avatar = root / "avatar.png"
            avatar.write_bytes(png)
            connection = connect(database)
            try:
                row = connection.execute("SELECT media_candidate_id FROM performer_media_candidates ORDER BY media_candidate_id LIMIT 1").fetchone()
                connection.execute(
                    """
                    UPDATE performer_media_candidates SET download_status='downloaded', local_path=?,
                      sha256=?, mime_type='image/png', width=256, height=256, byte_size=?
                    WHERE media_candidate_id=?
                    """,
                    (str(avatar), "0" * 64, len(png), row["media_candidate_id"]),
                )
                connection.commit()
            finally:
                connection.close()
            showcase = build_actor_showcase(database, root / "showcase")
            exported = export_selfdeepsearch(database, root / "export")
            catalog = json.loads((root / "showcase" / "performers.json").read_text(encoding="utf-8"))
            performer_staging = [json.loads(line) for line in (root / "export" / "performer_media_staging.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result["performers"], 2)
        self.assertEqual(result["avatar_candidates"], 2)
        self.assertEqual(showcase["avatars"], 1)
        self.assertEqual(catalog["counts"]["performers"], 2)
        self.assertEqual(exported["performers"], 2)
        self.assertEqual(len(performer_staging), 2)
        self.assertTrue(all(item["entity_type"] == "performer" for item in performer_staging))


if __name__ == "__main__":
    unittest.main()
