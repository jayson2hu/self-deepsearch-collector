"""Build an offline, deterministic collection demonstration. Never accesses the network."""

from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from collector.contracts import ContractError, content_hash, validate_candidate

ROOT = Path(__file__).resolve().parents[3]
STAMP = "2026-09-16T08:00:00Z"
VERSION = "collection-demo@v1"
TITLES = [
    "雨后的城市", "海岸来信", "午后的光", "漫长的周末", "山间日记", "窗边的风景",
    "夜航记录", "夏日序曲", "留在街角的声音", "蓝色时刻", "四月的散步", "未完成的旅行",
    "远方的灯塔", "白昼与星辰", "晴天手记", "一座城的侧面", "时间的纹理", "沿途风光",
    "风经过的地方", "平行的日常", "雨季片段", "昨日的明信片", "风与树的对话", "缓慢的季节",
    "海风中的书页", "第七个清晨", "街灯亮起之前", "北方的车站", "一日一景", "春天的回声",
    "旅途中的一封长信：那些没有标记在地图上的小小风景", "另一种晴朗", "城市边缘", "岛屿之间",
    "", "",
]
STUDIOS = ["青屿影像", "远山制作", "白昼工作室", "回声档案"]
PEOPLE = ["示例人物 A", "示例人物 B", "示例人物 C", "示例人物 D", "示例人物 E", "示例人物 F", "示例人物 G", "示例人物 H"]


def normalize_code(raw: str) -> str:
    value = unicodedata.normalize("NFKC", raw).strip().upper()
    value = re.sub(r"[\s_‐‑–—−]+", "-", value)
    return re.sub(r"-+", "-", value)


def make_record(index: int, source: str = "fixture_catalog", **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "canonical_code": f"DEMO-{index + 1:03}",
        "title": TITLES[index],
        "release_date": None if index in (6, 28) else (date(2026, 9, 15) - timedelta(days=index * 3)).isoformat(),
        "studio_name": STUDIOS[index % len(STUDIOS)],
        "performer_aliases": [PEOPLE[index % len(PEOPLE)]],
    }
    if index % 5 == 0:
        payload["performer_aliases"].append(PEOPLE[(index + 3) % len(PEOPLE)])
    payload.update(changes)
    digest = content_hash(payload)
    return {
        "source_id": source,
        "demo_entity_id": f"demo-work-{index + 1:03}",
        "external_id": f"{source}-{index + 1:03}",
        "entity_type": "work",
        "operation": "upsert",
        "source_updated_at": None,
        "idempotency_key": f"{source}:work:{index + 1:03}:{digest}",
        "content_hash": digest,
        "payload": payload,
        "provenance": {
            "source_type": "fixture",
            "source_url": None,
            "source_title": "本地合成目录样本" if source == "fixture_catalog" else "本地合成补充样本",
            "checked_at": STAMP,
            "confidence": 1,
            "rights_status": "needs_review",
        },
    }


def fixture_records() -> list[dict[str, Any]]:
    records = [make_record(i) for i in range(36)]
    records.extend(make_record(i, "fixture_reference", release_date="2026-06-01") for i in (32, 33))
    records.extend(make_record(i, "fixture_reference") for i in (0, 1))
    records.extend(copy.deepcopy(records[i]) for i in (0, 1, 2))
    return records


def process_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Fixture grouping is explicitly supplied, never inferred from real-world names/codes."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    keys: dict[tuple[str, str], str] = {}
    duplicates = 0
    for record in records:
        validate_candidate(record)
        if record["provenance"]["source_type"] != "fixture":
            raise ContractError("demo accepts only synthetic fixture records")
        source = record["source_id"]
        key = (source, record["idempotency_key"])
        if key in keys and keys[key] != record["content_hash"]:
            raise ContractError("idempotency key reused with a different payload")
        keys[key] = record["content_hash"]
        identity = (source, record["entity_type"], record["external_id"], record["content_hash"])
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        groups[record["demo_entity_id"]].append(record)

    works = []
    for entity_id, observations in groups.items():
        payload = copy.deepcopy(observations[0]["payload"])
        payload["canonical_code"] = normalize_code(payload["canonical_code"])
        errors = []
        if not payload["title"].strip():
            errors.append("缺少资料标题，需补全后重新送审")
        if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", payload["canonical_code"]):
            errors.append("番号格式无效")
        for observation in observations:
            release = observation["payload"].get("release_date")
            if release is not None:
                try:
                    date.fromisoformat(release)
                except (TypeError, ValueError):
                    errors.append("发行日期格式无效")
        conflicts = []
        for field in ("title", "release_date", "studio_name", "performer_aliases"):
            values: list[dict[str, Any]] = []
            for observation in observations:
                value = observation["payload"].get(field)
                if value is not None and value not in [item["value"] for item in values]:
                    values.append({"value": value, "source": observation["provenance"]["source_title"]})
            if len(values) > 1:
                conflicts.append({"field": field, "values": values})
        works.append({
            "id": entity_id,
            **payload,
            "status": "invalid" if errors else "conflict" if conflicts else "pending",
            "errors": errors,
            "conflicts": conflicts,
            "evidence": [{
                "external_id": item["external_id"],
                "content_hash": item["content_hash"],
                "payload": item["payload"],
                **item["provenance"],
            } for item in observations],
            "updated_at": STAMP,
        })
    return {
        "works": works,
        "report": {
            "input_records": len(records),
            "unique_observations": len(seen),
            "duplicate_records": duplicates,
            "entities": len(works),
            "conflicts": sum(work["status"] == "conflict" for work in works),
            "invalid": sum(work["status"] == "invalid" for work in works),
            "missing_release_date": sum(work["release_date"] is None for work in works),
        },
    }


def build_dataset() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = fixture_records()
    result = process_records(records)
    result.update({
        "version": VERSION,
        "built_at": STAMP,
        "synthetic": True,
        "network_access": False,
        "notice": "全部人物、厂牌、作品和统计均为合成演示；不是 JavDB 或 Jable 抓取结果。",
        "initial_published_ids": [work["id"] for work in result["works"][:24]],
        "performers": [{"id": f"demo-person-{i + 1}", "name": name, "aliases": [f"DEMO PERSON {chr(65 + i)}"]} for i, name in enumerate(PEOPLE)],
        "studios": [{"id": f"demo-studio-{i + 1}", "name": name} for i, name in enumerate(STUDIOS)],
        "registry": json.loads((ROOT / "data/collection/source-registry.json").read_text(encoding="utf-8")),
        "task_templates": json.loads((ROOT / "data/collection/task-templates.json").read_text(encoding="utf-8")),
    })
    return result, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    dataset, records = build_dataset()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "candidates.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    print(json.dumps(dataset["report"], ensure_ascii=False))


if __name__ == "__main__":
    main()
