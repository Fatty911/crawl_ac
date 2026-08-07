#!/usr/bin/env python3
"""Merge crawler outputs, deduplicate AC model identities, enforce publication rules.

Publication gate (fail closed, per user's buying guide):
- ac_type must be 壁挂式 or 立柜式 (central/mobile AC never published);
- inverter must be True (定频 rejected);
- APF must meet the tier minimum: 壁挂式 → >= 5.0 (preferred >= 5.3),
  立柜式 → >= 4.2 (preferred >= 4.5); unknown APF fails closed.
- 节流装置 (throttle_type) and 铜管排数 (coil_rows) are NOT hard gates but
  every published record must carry the field (unknown allowed, UI marks it);
  the frontend can filter by them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_ALIASES = {
    "zol": "ZOL",
    "中关村": "ZOL",
    "中关村在线": "ZOL",
    "detail.zol.com.cn": "ZOL",
    "jd": "JD",
    "jd.com": "JD",
    "京东": "JD",
    "京东商城": "JD",
    "京东自营": "JD",
    "pconline": "PConline",
    "太平洋电脑网": "PConline",
    "太平洋": "PConline",
    "product.pconline.com.cn": "PConline",
}

BRAND_ALIASES = {
    "gree": "格力",
    "midea": "美的",
    "haier": "海尔",
    "aux": "奥克斯",
    "tcl": "TCL",
    "hisense": "海信",
    "kelon": "科龙",
    "changhong": "长虹",
    "wahin": "华凌",
    "华凌": "华凌",
    "xiaomi": "小米",
    "米家": "小米",
    "daikin": "大金",
    "mitsubishi": "三菱电机",
    "panasonic": "松下",
    "chigo": "志高",
    "skyworth": "创维",
    "konka": "康佳",
}

# 空调型号模式：KFR-35GW/N8HA1Ⅲ-P、KFR-72LW/N8KS1-1U、KF-26GW/...、KFRD-...
AC_MODEL_PATTERN = re.compile(
    r"KFRD?|KFD?|KF"  # KF=单冷 KFR=冷暖 KFRD=带电辅
)

# 罗马数字/特殊字符归一
_ROMAN = str.maketrans({"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V",
                        "Ⅵ": "VI", "Ⅶ": "VII", "Ⅷ": "VIII", "Ⅸ": "IX", "Ⅹ": "X"})


def normalize_model_identity(text: Any) -> str:
    """Extract the canonical AC model identity from a title/spec value.

    Examples:
      '华凌KFR-35GW/N8HA1Ⅲ-H' -> 'kfr35gw/n8ha1iii-h'
      '美的空调 1.5匹 KFR-35GW/N8HA1Ⅲ-P' -> 'kfr35gw/n8ha1iii-p'
      'KFR-72LW/N8KS1-1U' -> 'kfr72lw/n8ks1-1u'
    Unknown input returns '' (fail closed downstream).
    """
    original = unicodedata.normalize("NFKC", str(text or "")).translate(_ROMAN)
    match = re.search(
        r"(KFRD?|KFD?|KF)\s*[-－]?\s*(\d{2,3})\s*([A-Z]{1,6})?\s*/?\s*([0-9A-Za-zⅢⅣ()\-]{2,40})",
        original,
    )
    if not match:
        return ""
    head = (match.group(1) or "").lower()
    capacity = match.group(2)
    suffix = re.sub(r"\s+", "", (match.group(3) or "").upper())
    variant = re.sub(r"[\s_\-]+", "-", (match.group(4) or "").upper())
    variant = variant.strip("-")
    identity = f"{head}{capacity}{suffix}/{variant}".lower()
    return identity


def normalize_brand(brand: Any) -> str:
    text = str(brand or "").strip()
    lowered = text.lower()
    if lowered in BRAND_ALIASES:
        return BRAND_ALIASES[lowered]
    for key, value in BRAND_ALIASES.items():
        if len(key) > 1 and key in text:
            return value
    return text


def parse_hp(value: Any) -> float | None:
    """'1.5匹' -> 1.5 ; '大1.5匹' -> 1.5 ; '3匹' -> 3.0"""
    text = str(value or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*匹", text)
    if match:
        return float(match.group(1))
    if "两匹" in text or "二匹" in text:
        return 2.0
    return None


def parse_apf(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(match.group(1)) if match else None


def tier_apf_floor(ac_type: str) -> float:
    """APF 及格线：壁挂式 >=5.0，立柜式 >=4.2（用户"核心参数速查"）。"""
    return 4.2 if "柜" in (ac_type or "") else 5.0


def tier_apf_preferred(ac_type: str) -> float:
    return 4.5 if "柜" in (ac_type or "") else 5.3


def check_publication(item: dict[str, Any]) -> tuple[bool, list[str]]:
    """Fail-closed publication gate; returns (ok, reasons)."""
    reasons: list[str] = []
    ac_type = str(item.get("ac_type") or "").strip()
    if ac_type not in ("壁挂式", "立柜式"):
        reasons.append(f"ac_type={ac_type or '未知'}（仅壁挂/立柜可发布）")
    inverter = item.get("inverter")
    if inverter is not True:
        reasons.append(f"inverter={inverter}（定频/未知拒绝）")
    apf = parse_apf(item.get("apf"))
    if apf is None:
        reasons.append("apf 未知（fail closed）")
    else:
        floor = tier_apf_floor(ac_type)
        if apf < floor:
            reasons.append(f"apf={apf} < 及格线 {floor}")
    # 节流装置/铜管排数非硬门槛，但必须带字段（unknown 允许，前端标注）
    if "throttle_type" not in item:
        item["throttle_type"] = "未知"
    if "coil_rows" not in item:
        item["coil_rows"] = "未知"
    return (not reasons), reasons


def merge_group(identity: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge rows sharing one identity; evidence-ranked field union."""
    merged: dict[str, Any] = {
        "identity_key": identity,
        "title": "",
        "model": "",
        "brand": "",
        "source_count": len(rows),
        "atomic_source_names": [],
        "source_urls": [],
        "source_ranks": [],
        "evidence": {},
    }
    # 字段优先级：详情页数据 > 列表页数据 > 标题推断；同一字段取第一个非空
    ranked_rows = sorted(
        rows,
        key=lambda r: (0 if r.get("detail_url") else 1),
    )
    for row in ranked_rows:
        for key, value in row.items():
            if key in ("source", "source_rank", "source_product_id", "source_url",
                       "atomic_source_names", "source_category", "source_count"):
                continue
            if key in merged and merged[key] not in ("", None, [], {}):
                continue
            if value in ("", None, [], {}):
                continue
            merged[key] = value
    # 品牌：取众数/首个非空
    brands = [normalize_brand(r.get("brand")) for r in rows if r.get("brand")]
    merged["brand"] = max(set(brands), key=brands.count) if brands else ""
    # 来源聚合
    seen_sources: list[str] = []
    for row in rows:
        for source in (row.get("atomic_source_names") or [row.get("source")]):
            name = SOURCE_ALIASES.get(str(source).lower(), str(source))
            if name and name not in seen_sources:
                seen_sources.append(name)
    merged["atomic_source_names"] = seen_sources
    merged["source_urls"] = [r["source_url"] for r in rows if r.get("source_url")]
    merged["source_ranks"] = [
        {"source": SOURCE_ALIASES.get(str(r.get("source", "")).lower(), str(r.get("source", ""))),
         "rank": r.get("source_rank")}
        for r in rows if r.get("source_rank") is not None
    ]
    # 标题：品牌 + 型号（多源时用详情页确认的型号）
    model = merged.get("model") or identity
    brand = merged.get("brand") or ""
    merged["title"] = f"{brand}{model}" if brand and not str(merged.get("title", "")).startswith(brand) else (merged.get("title") or f"{brand}{model}")
    return merged


def load_source_artifact(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"artifact load failed {path}: {exc}")
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="AC merge and gate")
    parser.add_argument("artifacts", nargs="+", help="source artifact json files")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rejected-output", required=True)
    parser.add_argument("--min-source-records", type=int, default=50)
    args = parser.parse_args()

    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for artifact_path in args.artifacts:
        items = load_source_artifact(Path(artifact_path))
        if not items:
            print(f"WARNING: empty artifact {artifact_path}")
            continue
        source = str(items[0].get("source") or Path(artifact_path).stem)
        rows_by_source[source] = items
        print(f"{source}: {len(items)} records")

    if len(rows_by_source) < 1:
        print("FAIL: no usable source artifacts")
        return 2
    for source, items in rows_by_source.items():
        if len(items) < args.min_source_records:
            print(f"FAIL: {source} only {len(items)} records "
                  f"(< {args.min_source_records})")
            return 1

    # 身份分组（型号归一）
    groups: dict[str, list[dict[str, Any]]] = {}
    unidentified: list[dict[str, Any]] = []
    for source, items in rows_by_source.items():
        for item in items:
            identity = normalize_model_identity(
                item.get("model") or item.get("title")
            )
            if not identity:
                identity = normalize_model_identity(item.get("title"))
            if not identity:
                unidentified.append(item)
                continue
            item.setdefault("source", source)
            groups.setdefault(identity, []).append(item)
    print(f"identities: {len(groups)}, unidentified: {len(unidentified)}")

    merged_items = [merge_group(identity, rows) for identity, rows in groups.items()]
    merged_items.sort(key=lambda m: m["source_count"], reverse=True)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in merged_items:
        ok, reasons = check_publication(item)
        if ok:
            accepted.append(item)
        else:
            item["reject_reasons"] = reasons
            rejected.append(item)
    print(f"accepted={len(accepted)} rejected={len(rejected)}")

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(accepted),
        "sources": list(rows_by_source.keys()),
        "pipeline": {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "identities": len(groups),
        },
        "items": accepted,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    rej = Path(args.rejected_output)
    rej.parent.mkdir(parents=True, exist_ok=True)
    rej.write_text(
        json.dumps({"count": len(rejected), "items": rejected},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"candidate written: {out} ({len(accepted)} accepted)")
    if not accepted:
        print("FAIL: zero accepted records")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
