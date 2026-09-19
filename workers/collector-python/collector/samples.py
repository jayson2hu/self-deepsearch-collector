from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from collector.connectors.javdb import CONNECTOR_VERSION, SOURCE_ID, parse_detail
from collector.contracts import ContractError

MAX_SAMPLES = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def parse_manifest(manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_id") != SOURCE_ID:
        raise ContractError(f"sample parser only accepts source_id={SOURCE_ID}")
    if manifest.get("connector_version") != CONNECTOR_VERSION:
        raise ContractError(f"connector_version must be {CONNECTOR_VERSION}")
    checked_at = manifest.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at:
        raise ContractError("checked_at is required")
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SAMPLES:
        raise ContractError(f"samples must contain between 1 and {MAX_SAMPLES} entries")

    candidates: list[dict[str, Any]] = []
    report_samples: list[dict[str, Any]] = []
    external_ids: set[str] = set()
    for index, item in enumerate(samples, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not isinstance(item.get("url"), str):
            raise ContractError(f"sample {index} requires file and url")
        sample_path = (manifest_path.parent / item["file"]).resolve()
        if manifest_path.parent.resolve() not in sample_path.parents:
            raise ContractError(f"sample {index} escapes the manifest directory")
        raw = sample_path.read_bytes()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ContractError(f"sample {index} exceeds {MAX_RESPONSE_BYTES} bytes")
        candidate = parse_detail(raw.decode("utf-8"), source_url=item["url"], checked_at=checked_at)
        external_id = str(candidate["external_id"])
        if external_id in external_ids:
            raise ContractError(f"duplicate external_id in manifest: {external_id}")
        external_ids.add(external_id)
        candidates.append(candidate)
        payload = candidate["payload"]
        assert isinstance(payload, dict)
        report_samples.append({
            "file": item["file"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "url": item["url"],
            "external_id": external_id,
            "canonical_code": payload.get("canonical_code"),
            "fields_present": [key for key, value in payload.items() if value not in (None, "", [])],
            "fields_missing": [key for key, value in payload.items() if value in (None, "", [])],
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "candidates.jsonl").write_text(
        "".join(json.dumps(candidate, ensure_ascii=False) + "\n" for candidate in candidates),
        encoding="utf-8",
    )
    report = {
        "source_id": SOURCE_ID,
        "connector_version": CONNECTOR_VERSION,
        "checked_at": checked_at,
        "input_samples": len(samples),
        "valid_candidates": len(candidates),
        "network_access": False,
        "samples": report_samples,
    }
    (output_dir / "parse-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse explicitly saved real-source HTML samples")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = parse_manifest(args.manifest, args.output_dir)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ContractError) as exc:
        print(json.dumps({"level": "error", "code": "SAMPLE_PARSE_ERROR", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
