from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.connectors.javdb_actors import (  # noqa: E402
    javdb_actor_list_page_url, parse_javdb_actor_list, parse_javdb_actor_profile,
    parse_javdb_actors, validate_javdb_actor_list_url, validate_javdb_actor_profile_url,
)
from collector.performer_assets import (  # noqa: E402
    build_actor_showcase, ingest_javdb_actor_candidates, ingest_javdb_actor_snapshot,
)
from collector.pipeline import export_selfdeepsearch  # noqa: E402
from collector.storage import connect  # noqa: E402


HTML = """<!doctype html><html><body><div id="actors">
<div class="box actor-box"><a href="/actors/AAA1" title="演员甲, 别名甲"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg"></figure></a></div>
<div class="box actor-box"><a href="/actors/BBB2" title="演员乙"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/bb/BBB2.jpg"></figure></a></div>
<div class="box actor-box"><a href="/actors/AAA1" title="演员甲, 别名甲"><figure><img class="avatar" src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg"></figure></a></div>
</div></body></html>"""


class PerformerAssetTests(unittest.TestCase):
    def parse(self, sample: str, *, checked_at: str = "2026-09-18T00:00:00Z") -> list[dict[str, object]]:
        return parse_javdb_actor_list(sample, source_url="https://javdb.com/actors", checked_at=checked_at)

    def test_actor_listing_extracts_unique_real_avatar_candidates(self) -> None:
        candidates = parse_javdb_actor_list(
            HTML, source_url="https://javdb.com/actors", checked_at="2026-09-18T00:00:00Z",
        )
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["payload"]["aliases"], ["演员甲", "别名甲"])
        self.assertEqual(candidates[0]["media"][0]["purpose"], "avatar")

    def test_actor_without_avatar_preserves_name_aliases_and_profile(self) -> None:
        candidates = self.parse('<a href="/actors/AAA1" title="演员甲, 别名甲"></a>')
        self.assertEqual(candidates[0]["payload"], {
            "name": "演员甲", "aliases": ["演员甲", "别名甲"],
            "profile_url": "https://javdb.com/actors/AAA1",
        })
        self.assertEqual(candidates[0]["media"], [])

    def test_duplicate_actor_does_not_erase_alias_or_avatar(self) -> None:
        sample = HTML + '<a href="/actors/AAA1" title="演员甲, 新别名"></a>'
        actor = self.parse(sample)[0]
        self.assertEqual(actor["payload"]["aliases"], ["演员甲", "别名甲", "新别名"])
        self.assertEqual(len(actor["media"]), 1)

    def test_visible_name_and_lazy_avatar_can_supply_missing_attributes(self) -> None:
        actor = self.parse('<a href="/actors/AAA1"><img class="avatar" src="/placeholder.jpg" '
                           'data-src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg">'
                           '<script>do_not_collect_script_text</script><h3>演员甲</h3></a>')[0]
        self.assertEqual(actor["payload"]["name"], "演员甲")
        self.assertEqual(len(actor["media"]), 1)

    def test_non_actor_and_noncanonical_links_are_not_imported(self) -> None:
        invalid_links = [
            "https://example.com/actors/BAD", "//example.com/actors/BAD",
            "http://javdb.com/actors/BAD", "https://javdb.com:8443/actors/BAD",
            "https://user:pass@javdb.com/actors/BAD", "/actors/BAD?token=hidden",
            "/actors/BAD#fragment", "/actors/BAD;session=hidden", "/actors/BAD/works",
            "/actors/censored", "/actors/uncensored", "/actors/western",
        ]
        for link in invalid_links:
            with self.subTest(link=link):
                sample = HTML + f'<a href="{link}" title="不应采集"><img class="avatar" src="https://c0.jdbstatic.com/avatars/ba/BAD.jpg"></a>'
                self.assertEqual(len(self.parse(sample)), 2)

    def test_invalid_avatar_is_omitted_but_actor_is_retained(self) -> None:
        for url in [
            "https://example.com/avatars/aa/AAA1.jpg",
            "https://c0.jdbstatic.com:8443/avatars/aa/AAA1.jpg",
            "https://c0.jdbstatic.com/covers/aa/AAA1.jpg",
            "https://c0.jdbstatic.com/avatars/aa/AAA1.jpg?token=hidden",
            "https://c0.jdbstatic.com/avatars/aa/AAA1.jpg;session=hidden",
        ]:
            with self.subTest(url=url):
                actor = self.parse(f'<a href="/actors/AAA1" title="演员甲"><img class="avatar" src="{url}"></a>')[0]
                self.assertEqual(actor["media"], [])

    def test_listing_pagination_accepts_only_bounded_page_query(self) -> None:
        source = "https://javdb.com/actors/censored?page=2"
        actors = parse_javdb_actor_list(HTML, source_url=source, checked_at="2026-09-18T00:00:00Z")
        self.assertEqual(actors[0]["provenance"]["source_url"], source)
        self.assertEqual(javdb_actor_list_page_url(source, 3), "https://javdb.com/actors/censored?page=3")
        self.assertEqual(validate_javdb_actor_list_url("https://javdb.com/actors?page=1000"), "https://javdb.com/actors?page=1000")
        for query in ["page=0", "page=-1", "page=1.5", "page=1001", "page=2&page=3", "page=2&sort=name", "page=02", "page=%32", "token=hidden"]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_javdb_actor_list_url(f"https://javdb.com/actors?{query}")
        for page in [0, -1, 1001, 1.5, True, "2"]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                javdb_actor_list_page_url(source, page)

    def test_content_hash_is_independent_of_collection_time(self) -> None:
        before = self.parse(HTML, checked_at="2026-09-18T00:00:00Z")
        after = self.parse(HTML, checked_at="2026-09-22T00:00:00Z")
        self.assertEqual([item["content_hash"] for item in before], [item["content_hash"] for item in after])
        self.assertNotEqual(before[0]["media"][0]["checked_at"], after[0]["media"][0]["checked_at"])
        changed = self.parse(HTML.replace("别名甲", "新别名甲"))
        self.assertNotEqual(before[0]["content_hash"], changed[0]["content_hash"])

    def test_actor_profile_extracts_only_explicit_subject_fields(self) -> None:
        sample = '''<title>Unrelated marketing page title</title>
        <meta property="og:title" content="Do not use this name">
        <main><section class="actor-profile" data-actor-id="AAA1">
        <div itemscope itemtype="https://schema.org/Person" itemid="https://javdb.com/actors/AAA1">
        <h1 itemprop="name">演员甲<script>do_not_collect_script_text</script></h1><span itemprop="alternateName">别名甲, Alias A</span>
        <meta itemprop="birthDate" content="1990-01-02"><span data-performer-field="height">160 cm</span>
        <span itemprop="birthPlace" itemscope itemtype="https://schema.org/Place"><span itemprop="name">东京</span></span>
        <img itemprop="image" src="https://c0.jdbstatic.com/avatars/aa/AAA1.jpg">
        <div class="related-actors"><h1>演员乙</h1><span data-performer-field="height">999 cm</span>
        <img class="avatar" src="https://c0.jdbstatic.com/avatars/bb/BBB2.jpg"></div>
        </div></section></main>'''
        actor = parse_javdb_actor_profile(sample, source_url="https://javdb.com/actors/AAA1", checked_at="2026-09-22T00:00:00Z")
        self.assertEqual(actor["payload"], {
            "name": "演员甲", "aliases": ["演员甲", "别名甲", "Alias A"],
            "profile_url": "https://javdb.com/actors/AAA1", "birth_date": "1990-01-02",
            "height": "160 cm", "birth_place": "东京",
        })
        self.assertEqual(actor["media"][0]["candidate_url"], "https://c0.jdbstatic.com/avatars/aa/AAA1.jpg")
        later = parse_javdb_actors(sample, source_url="https://javdb.com/actors/AAA1", checked_at="2026-09-23T00:00:00Z")
        self.assertEqual(later[0]["content_hash"], actor["content_hash"])

    def test_profile_never_guesses_names_from_title_slug_or_related_actors(self) -> None:
        samples = [
            '<title>演员甲 - JavDB</title><meta property="og:title" content="演员甲">',
            '<aside><h1>演员乙</h1></aside>',
            '<a href="/actors/BBB2"><h1>演员乙</h1></a>',
            '<h1>演员甲</h1><h1>演员乙</h1>',
            '<h1>Not Found</h1>',
            '<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">演员甲</span></div>'
            '<div itemscope itemtype="https://schema.org/Person"><span itemprop="name">演员乙</span></div>',
        ]
        for sample in samples:
            with self.subTest(sample=sample), self.assertRaisesRegex(ValueError, "unsupported"):
                parse_javdb_actor_profile(sample, source_url="https://javdb.com/actors/AAA1", checked_at="2026-09-22T00:00:00Z")

    def test_profile_url_is_strict_and_simple_name_does_not_require_avatar(self) -> None:
        self.assertEqual(validate_javdb_actor_profile_url("https://javdb.com/actors/AAA1"), "https://javdb.com/actors/AAA1")
        for url in ["/actors/AAA1", "https://example.com/actors/AAA1", "https://javdb.com/actors/censored", "https://javdb.com/actors/AAA1?page=2", "https://javdb.com/v/AAA1", "https://javdb.com:8443/actors/AAA1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_javdb_actor_profile_url(url)
        actor = parse_javdb_actors('<main><h1>演员甲</h1></main>', source_url="https://javdb.com/actors/AAA1", checked_at="2026-09-22T00:00:00Z")[0]
        self.assertEqual(actor["payload"]["name"], "演员甲")
        self.assertEqual(actor["media"], [])

    def test_memory_ingest_is_idempotent_and_preserves_historical_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collector.db"
            first = ingest_javdb_actor_candidates(self.parse(HTML), database, checked_at="2026-09-18T00:00:00Z")
            second = ingest_javdb_actor_candidates(self.parse(HTML, checked_at="2026-09-19T00:00:00Z"), database, checked_at="2026-09-19T00:00:00Z")
            self.assertEqual(first["new_observations_this_run"], 2)
            self.assertEqual(second["new_observations_this_run"], 0)
            connection = connect(database)
            try:
                connection.execute("UPDATE performer_media_candidates SET download_status='downloaded', local_path='/existing/avatar.jpg', review_status='approved'")
                connection.commit()
            finally:
                connection.close()
            latest = self.parse('<a href="/actors/AAA1" title="演员甲"></a>', checked_at="2026-09-22T00:00:00Z")
            report = ingest_javdb_actor_candidates(latest, database, checked_at="2026-09-22T00:00:00Z")
            # Reimporting an older snapshot is a valid observation, but cannot roll current data back.
            ingest_javdb_actor_candidates(self.parse(HTML), database, checked_at="2026-09-18T00:00:00Z")
            connection = connect(database)
            try:
                current = connection.execute("SELECT * FROM performers WHERE external_id='AAA1'").fetchone()
                media = connection.execute("SELECT * FROM performer_media_candidates WHERE external_id='AAA1'").fetchone()
                observations = [json.loads(row[0]) for row in connection.execute("SELECT candidate_json FROM performer_observations WHERE external_id='AAA1'")]
            finally:
                connection.close()
            self.assertEqual(report["observations"], 3)
            self.assertEqual(current["checked_at"], "2026-09-22T00:00:00Z")
            self.assertEqual(current["current_content_hash"], latest[0]["content_hash"])
            self.assertEqual(media["download_status"], "downloaded")
            self.assertEqual(media["review_status"], "approved")
            self.assertEqual(media["local_path"], "/existing/avatar.jpg")
            self.assertEqual(media["checked_at"], "2026-09-19T00:00:00Z")
            self.assertTrue(any(item["payload"]["aliases"] == ["演员甲", "别名甲"] for item in observations))
            self.assertTrue(any(item["payload"]["aliases"] == ["演员甲"] and item["media"] == [] for item in observations))
            self.assertEqual(list(Path(directory).glob("*.html")), [])

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
