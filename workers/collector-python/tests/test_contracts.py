from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.cli import main  # noqa: E402
from collector.contracts import ContractError, content_hash, validate_candidate  # noqa: E402


class ContractTests(unittest.TestCase):
    def test_valid_candidate(self) -> None:
        payload = {"canonical_code": "TEST-001", "title": "Synthetic"}
        candidate = {
            "external_id": "fixture-work-001",
            "entity_type": "work",
            "operation": "upsert",
            "idempotency_key": "fixture:work:fixture-work-001:v1",
            "content_hash": content_hash(payload),
            "payload": payload,
            "provenance": {
                "source_type": "fixture",
                "checked_at": "2026-08-05T00:00:00Z",
                "confidence": 1,
                "rights_status": "needs_review",
            },
        }
        validate_candidate(candidate)

    def test_hash_mismatch_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            validate_candidate({
                "external_id": "id",
                "entity_type": "work",
                "operation": "upsert",
                "idempotency_key": "key",
                "content_hash": "sha256:" + "0" * 64,
                "payload": {"title": "Synthetic"},
                "provenance": {"source_type": "fixture", "checked_at": "2026-08-05T00:00:00Z", "confidence": 1, "rights_status": "needs_review"},
            })

    def test_probe_rejects_real_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exit_code = main(["probe", "--source-id", "real-source", "--output-dir", directory])
        self.assertEqual(exit_code, 2)

    def test_collect_requires_network_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope_file = root / "scope.json"
            scope_file.write_text(json.dumps({"network_access": True}), encoding="utf-8")
            exit_code = main([
                "collect", "--source-id", "fixture", "--connector-version", "fixture@v1",
                "--scope-file", str(scope_file), "--output-dir", str(root / "output"),
            ])
        self.assertEqual(exit_code, 2)

    def test_collect_copies_valid_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope_file = root / "scope.json"
            output = root / "output"
            scope_file.write_text(json.dumps({"network_access": False}), encoding="utf-8")
            exit_code = main([
                "collect", "--source-id", "fixture", "--connector-version", "fixture@v1",
                "--scope-file", str(scope_file), "--output-dir", str(output),
            ])
            candidates = (output / "candidates.jsonl").read_text(encoding="utf-8")
        self.assertEqual(exit_code, 0)
        self.assertIn("fixture-work-001", candidates)


if __name__ == "__main__":
    unittest.main()
