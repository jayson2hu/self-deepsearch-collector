from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from collector import __version__
from collector.contracts import ContractError, validate_candidate

FIXTURE_SOURCE = "fixture"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Release A collection contract simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Describe the fixture connector")
    probe.add_argument("--source-id", required=True)
    probe.add_argument("--connector-version", default="fixture@v1")
    probe.add_argument("--output-dir", type=Path, required=True)

    collect = subparsers.add_parser("collect", help="Copy reviewed fixture candidates")
    collect.add_argument("--source-id", required=True)
    collect.add_argument("--connector-version", required=True)
    collect.add_argument("--scope-file", type=Path, required=True)
    collect.add_argument("--output-dir", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="Validate candidate JSONL")
    validate.add_argument("--input", type=Path, required=True)
    return parser


def command_probe(args: argparse.Namespace) -> int:
    if args.source_id != FIXTURE_SOURCE:
        raise ContractError("Release A only allows source-id=fixture")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "source_id": args.source_id,
        "connector_version": args.connector_version,
        "collector_version": __version__,
        "status": "fixture_only",
        "network_access": False,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    (args.output_dir / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def command_collect(args: argparse.Namespace) -> int:
    if args.source_id != FIXTURE_SOURCE or args.connector_version != "fixture@v1":
        raise ContractError("Release A only allows fixture@v1")
    scope = json.loads(args.scope_file.read_text(encoding="utf-8"))
    if scope.get("network_access") is not False:
        raise ContractError("scope must explicitly set network_access=false")
    fixture = Path(__file__).parent / "fixtures" / "candidates.jsonl"
    command_validate(argparse.Namespace(input=fixture))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, args.output_dir / "candidates.jsonl")
    (args.output_dir / "media_candidates.jsonl").write_text("", encoding="utf-8")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    for line_number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidate = json.loads(line)
            validate_candidate(candidate)
        except (json.JSONDecodeError, ContractError) as exc:
            raise ContractError(f"line {line_number}: {exc}") from exc
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            return command_probe(args)
        if args.command == "collect":
            return command_collect(args)
        return command_validate(args)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(json.dumps({"level": "error", "code": "CONTRACT_ERROR", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
