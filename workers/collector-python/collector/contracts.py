from __future__ import annotations

import hashlib
import json
from typing import Any

REQUIRED_FIELDS = {
    "external_id",
    "entity_type",
    "operation",
    "idempotency_key",
    "content_hash",
    "payload",
    "provenance",
}

REQUIRED_PROVENANCE = {
    "source_type",
    "checked_at",
    "confidence",
    "rights_status",
}


class ContractError(ValueError):
    """A candidate does not satisfy the Release B handoff contract."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(payload: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def validate_candidate(candidate: dict[str, Any]) -> None:
    missing = REQUIRED_FIELDS.difference(candidate)
    if missing:
        raise ContractError(f"missing fields: {', '.join(sorted(missing))}")
    if candidate["entity_type"] not in {"work", "performer", "studio"}:
        raise ContractError("entity_type is not allowed")
    if candidate["operation"] != "upsert":
        raise ContractError("only upsert is allowed")
    expected_hash = content_hash(candidate["payload"])
    if candidate["content_hash"] != expected_hash:
        raise ContractError("content_hash does not match payload")
    provenance = candidate["provenance"]
    if not isinstance(provenance, dict):
        raise ContractError("provenance must be an object")
    missing_provenance = REQUIRED_PROVENANCE.difference(provenance)
    if missing_provenance:
        raise ContractError(f"missing provenance fields: {', '.join(sorted(missing_provenance))}")
    confidence = provenance["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ContractError("confidence must be between 0 and 1")
