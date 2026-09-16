from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.contracts import ContractError, content_hash  # noqa: E402
from collector.demo import fixture_records, make_record, normalize_code, process_records  # noqa: E402


class DemoPipelineTests(unittest.TestCase):
    def test_replaying_batch_retains_entities_and_conflict_evidence(self):
        records = fixture_records()
        first = process_records(records)
        replay = process_records(records * 3)
        self.assertEqual(first["works"], replay["works"])
        self.assertEqual(first["report"]["entities"], 36)
        self.assertEqual(first["report"]["duplicate_records"], 3)
        self.assertEqual(replay["report"]["duplicate_records"], 89)
        self.assertEqual(first["report"]["conflicts"], 2)
        self.assertEqual(first["report"]["invalid"], 2)

    def test_same_code_does_not_merge_different_entities(self):
        records = [make_record(0), make_record(1, canonical_code="DEMO-001")]
        self.assertEqual(process_records(records)["report"]["entities"], 2)

    def test_conflicting_sources_retain_both_values_and_no_publication(self):
        records = [make_record(0), make_record(0, "fixture_reference", release_date="2026-01-01")]
        work = process_records(records)["works"][0]
        self.assertEqual(work["status"], "conflict")
        self.assertEqual(len(work["conflicts"][0]["values"]), 2)
        self.assertNotIn("publication_status", work)
        self.assertEqual(len(work["evidence"]), 2)

    def test_unknown_date_is_allowed_but_invalid_date_is_quarantined(self):
        unknown = process_records([make_record(0, release_date=None)])["works"][0]
        invalid = process_records([make_record(0, release_date="2026-02-30")])["works"][0]
        self.assertEqual(unknown["status"], "pending")
        self.assertIsNone(unknown["release_date"])
        self.assertEqual(invalid["status"], "invalid")

    def test_changed_payload_cannot_reuse_idempotency_key(self):
        first = make_record(0)
        second = make_record(0, title="Changed")
        second["idempotency_key"] = first["idempotency_key"]
        with self.assertRaises(ContractError):
            process_records([first, second])

    def test_corrupted_payload_fails_before_grouping(self):
        record = copy.deepcopy(make_record(0))
        record["payload"]["title"] = "tampered"
        with self.assertRaises(ContractError):
            process_records([record])

    def test_demo_cannot_accept_a_real_source(self):
        record = make_record(0)
        record["provenance"]["source_type"] = "public_web"
        with self.assertRaises(ContractError):
            process_records([record])

    def test_normalization_preserves_raw_evidence(self):
        record = make_record(0, canonical_code="ｄｅｍｏ—００１")
        self.assertEqual(record["content_hash"], content_hash(record["payload"]))
        work = process_records([record])["works"][0]
        self.assertEqual(work["canonical_code"], "DEMO-001")
        self.assertEqual(work["evidence"][0]["payload"]["canonical_code"], "ｄｅｍｏ—００１")
        self.assertEqual(normalize_code(" demo _ 001 "), "DEMO-001")


if __name__ == "__main__":
    unittest.main()
