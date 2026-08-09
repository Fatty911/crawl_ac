#!/usr/bin/env python3
"""Preserve all eligible identities from the previously published payload."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.merge_data import check_publication
except ModuleNotFoundError:
    from merge_data import check_publication


def _inherit_hardware_fields(item: dict[str, Any], baseline_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Candidate 版本缺失硬件参数时，从 baseline 同 identity 继承。

    merge 会用新爬取版本覆盖旧发布版本，旧版本上 AI 提取的
    throttle_type/coil_rows/hardware_evidence_url 会丢失。这里在发布前
    把 baseline 已知而 candidate 未知的硬件字段补回。
    """
    baseline_item = baseline_by_id.get(item.get("identity_key"))
    if not baseline_item:
        return item
    for field in ("throttle_type", "coil_rows", "hardware_evidence_url"):
        current = item.get(field)
        if current in (None, "", "未知"):
            inherited = baseline_item.get(field)
            if inherited not in (None, "", "未知"):
                item[field] = inherited
    return item


def _inherit_from_cache(item: dict[str, Any], cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从 git 硬件缓存继承（cache[identity] = {throttle_type, coil_rows, evidence_url}）。

    硬件缓存由 Hardware Enrich 提交 git（crawl_state/hardware_cache.json），
    merge 阶段直接应用，发布即恢复已提取参数，不等提取轮次。
    """
    entry = cache.get(item.get("identity_key"))
    if not entry:
        return item
    for field, cache_key in (("throttle_type", "throttle_type"),
                             ("coil_rows", "coil_rows"),
                             ("hardware_evidence_url", "evidence_url")):
        current = item.get(field)
        if current in (None, "", "未知"):
            inherited = entry.get(cache_key)
            if inherited not in (None, "", "未知"):
                item[field] = inherited
    return item


def load_cache(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def preserve(candidate: dict[str, Any], baseline: dict[str, Any] | None,
             cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    candidate_items = candidate.get("items", [])
    baseline_items = (baseline or {}).get("items", [])
    eligible_baseline = [item for item in baseline_items if check_publication(item)[0]]
    candidate_ids = {item.get("identity_key") for item in candidate_items}
    preserved = [
        item for item in eligible_baseline
        if item.get("identity_key") not in candidate_ids
    ]
    baseline_by_id = {item.get("identity_key"): item for item in eligible_baseline}
    cache = cache or {}
    # 继承：candidate 新版本补回 baseline 的硬件参数 + git 缓存
    candidate_items = [
        _inherit_from_cache(
            _inherit_hardware_fields(dict(item), baseline_by_id), cache
        )
        for item in candidate_items
    ]
    merged = [*candidate_items, *preserved]
    # 值归并（P/匹、新一级能效/新一级、2排/双排）——含缓存/基线继承值
    from merge_data import normalize_coil_rows, normalize_energy_grade, normalize_hp
    for item in merged:
        for field, normalizer in (("hp", normalize_hp),
                                  ("energy_grade", normalize_energy_grade),
                                  ("coil_rows", normalize_coil_rows)):
            if item.get(field):
                item[field] = normalizer(item[field])
    payload = {
        "schema_version": candidate.get("schema_version", "1.0"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(merged),
        "sources": candidate.get("sources", []),
        "pipeline": {
            **(candidate.get("pipeline") or {}),
            "candidate_count": len(candidate_items),
            "baseline_count": len(eligible_baseline),
            "preserved_count": len(preserved),
        },
        "items": merged,
    }
    return payload


def read_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--cache", default=None, help="git hardware cache json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = preserve(
        read_payload(Path(args.candidate)) or {"items": []},
        read_payload(Path(args.baseline)),
        load_cache(args.cache),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"baseline={result['pipeline']['baseline_count']} "
        f"candidate={result['pipeline']['candidate_count']} "
        f"preserved={result['pipeline']['preserved_count']} "
        f"published={result['count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
