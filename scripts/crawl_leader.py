#!/usr/bin/env python3
"""统帅（Leader）官网空调爬虫——curl 直连（官网 SSR + schema.org JSON-LD）。

列表页 https://www.leader.com.cn/air-conditioners/ （SSR，含全部产品 shtml 链接）
详情页 JSON-LD PropertyValue：匹数/能效等级/制冷量/制热量/功率/循环风量/尺寸等。

数据：官方一手参数（多源合并的权威佐证源）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.crawler_utils import make_session, get_html, parse_number
    from scripts.crawl_runtime import Budget, human_delay
    from scripts.node_rotator import make_rotator
except ModuleNotFoundError:
    from crawler_utils import make_session, get_html, parse_number
    from crawl_runtime import Budget, human_delay
    from node_rotator import make_rotator

LIST_URL = "https://www.leader.com.cn/air-conditioners/"

MODEL_RE = re.compile(
    r"(KFRD?|KFD?|KF)\s*[-－]?\s*(\d{2,3})\s*([A-Z]{1,6})?\s*/\s*([0-9A-Za-zⅢⅣ()\-]{2,30})",
    re.IGNORECASE)


def fetch(url: str, session: Any, delay: Any) -> str:
    html, _ = get_html(session, url, encoding="utf-8", delay=delay)
    return str(html)


def extract_links(html: str) -> list[str]:
    """列表页提取产品详情 shtml 链接（去重、限定 air-conditioners 路径）。"""
    links = []
    for m in re.finditer(r'href="(https://www\.leader\.com\.cn/air-conditioners/[^"]*\.shtml)"', html):
        url = m.group(1)
        if url not in links:
            links.append(url)
    return links


def extract_property_values(html: str) -> dict[str, str]:
    """详情页 JSON-LD PropertyValue → {name: value}。"""
    pvs: dict[str, str] = {}

    def collect(d: Any) -> None:
        if isinstance(d, dict):
            if d.get("@type") == "PropertyValue" and d.get("name"):
                val = str(d.get("value") or "").strip()
                unit = str(d.get("unitText") or "").strip()
                pvs[str(d["name"]).strip()] = f"{val} {unit}".strip()
            for v in d.values():
                collect(v)
        elif isinstance(d, list):
            for v in d:
                collect(v)

    for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            collect(json.loads(ld))
        except json.JSONDecodeError:
            continue
    return pvs


def map_specs(specs: dict[str, str], title: str) -> dict[str, Any]:
    """PropertyValue → 统一字段。"""
    model_m = MODEL_RE.search(title) or MODEL_RE.search(" ".join(specs.values()))
    if model_m:
        # 尾部 '-' 清洗（贪婪匹配吃到标题分隔符）
        model = re.sub(r"-+$", "", model_m.group(0).upper().replace(" ", ""))
    else:
        model = title[:40]
    item: dict[str, Any] = {
        "title": title,
        "model": model,
        "brand": "统帅",
        "source": "Leader",
        "atomic_source_names": ["Leader"],
        "source_category": "Leader official site",
        "currency": "CNY",
    }
    ptype = specs.get("产品类型", "")
    if "柜" in ptype or "立式" in ptype or "落地" in ptype:
        item["ac_type"] = "立柜式"
    elif "挂" in ptype or "壁挂" in ptype:
        item["ac_type"] = "壁挂式"
    hp = specs.get("匹数", "")
    if hp:
        item["hp"] = hp
    eg = specs.get("能效等级", "")
    if eg:
        item["energy_grade"] = eg
    inv = specs.get("变频/定频", "") or specs.get("变频", "")
    if "变频" in inv:
        item["inverter"] = True
    elif "定频" in inv:
        item["inverter"] = False
    for src_key, dst_key in (
        ("制冷量", "cooling_capacity"), ("制热量", "heating_capacity"),
        ("额定制冷功率", "cooling_power"), ("额定制热功率", "heating_power"),
        ("循环风量", "air_flow"),
    ):
        val = specs.get(src_key)
        if val:
            num = parse_number(val)
            if num:
                item[dst_key] = num
    refrigerant = specs.get("制冷剂", "")
    if refrigerant:
        item["refrigerant"] = refrigerant
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="Leader official site crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--min-records", type=int, default=5)
    parser.add_argument("--max-detail", type=int, default=0,
                        help="max detail pages to fetch (0=all)")
    args = parser.parse_args()

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())
    budget = Budget(args.time_budget)
    delay = human_delay(args.delay)

    list_html = fetch(LIST_URL, session, delay)
    links = extract_links(list_html)
    print(f"列表页产品链接: {len(links)}")

    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, url in enumerate(links):
        if budget.expired():
            print("time budget expired, stop")
            break
        if args.max_detail and idx >= args.max_detail:
            break
        try:
            html = fetch(url, session, delay)
        except Exception as exc:
            print(f"detail {url[-40:]} failed: {type(exc).__name__}")
            continue
        title_m = re.search(r"<title>([^<]{5,100})</title>", html)
        title = title_m.group(1).strip() if title_m else ""
        if not title:
            continue
        specs = extract_property_values(html)
        if not specs:
            print(f"detail {url[-40:]}: no specs")
            continue
        item = map_specs(specs, title)
        key = item["model"]
        if key in seen:
            continue
        seen.add(key)
        item["source_url"] = url
        all_items.append(item)
        print(f"+ {item['model']} ({item.get('ac_type','?')} {item.get('hp','?')} 能效{item.get('energy_grade','?')})")

    payload = {
        "schema_version": "1.0",
        "source": "Leader",
        "count": len(all_items),
        "items": all_items,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"artifact written: {out} ({len(all_items)} items)")
    if len(all_items) < args.min_records:
        print(f"FAIL: only {len(all_items)} records (< {args.min_records})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
