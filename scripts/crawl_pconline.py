#!/usr/bin/env python3
"""Crawl PConline air-conditioner brand pages (most-popular order) and enrich
each product from its full spec page.

Source facts verified 2026-08-07:
- Brand catalogue: https://product.pconline.com.cn/air_condition/{brand}/
  with a "最热门" (most popular) tab at list.shtml — the server-rendered
  list order IS the source's own popularity evidence (user requirement: no
  hard-coded popularity).
- List cards (``#JlistItems li.item``) carry core specs: 空调类型 / 适用面积 /
  产品功率 / 冷暖类型 / 制冷量 / 制热量.
- Full specs live at ``{pid}_detail.html`` (table.dtparams-table, th/td):
  系列名称 / 型号 / 上市时间 / 控制方式 / 扫风方式 / 是否变频 / 制冷量功率 /
  制热量功率 / 循环风量 / 噪音 / 能效等级 / APF / 制冷剂 / 尺寸 / 重量.
- Encoding is gb18030.  Pagination follows the ``.page-next`` link.

Hardware fields the spec table does NOT carry (节流装置 / 铜管排数) are left
unknown here and filled by scripts/ai_extract_hardware.py, which has to cite
positive evidence before they count as known.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from scripts.crawler_utils import (
        absolute_url,
        clean_text,
        get_html,
        make_session,
        parse_number,
        parse_price,
        utc_now,
    )
    from scripts.crawl_runtime import (
        Budget,
        Progress,
        append_jsonl,
        human_delay,
        item_key,
        merge_new_items,
        read_jsonl,
        rewrite_jsonl,
    )
    from scripts.node_rotator import make_rotator
except ModuleNotFoundError:
    from crawler_utils import (
        absolute_url,
        clean_text,
        get_html,
        make_session,
        parse_number,
        parse_price,
        utc_now,
    )
    from crawl_runtime import (
        Budget,
        Progress,
        append_jsonl,
        human_delay,
        item_key,
        merge_new_items,
        read_jsonl,
        rewrite_jsonl,
    )
    from node_rotator import make_rotator

BASE_URL = "https://product.pconline.com.cn"
CATALOGUE_URL = f"{BASE_URL}/air_condition/{{brand}}/"

# PConline 列表页"最热门"排序的产品卡与分页
LIST_SELECTOR = "#JlistItems li.item"
NEXT_PAGE_SELECTOR = "a.page-next[href]"

# 详情页参数名 → 统一 schema 字段
SPEC_FIELD_MAP = {
    "系列名称": "series",
    "型号(别称)": "model",
    "上市时间": "launch_date",
    "空调类型": "ac_type",
    "适用面积": "area",
    "控制方式": "control_mode",
    "扫风方式": "swing_mode",
    "产品功率": "hp",
    "冷暖类型": "cool_heat_type",
    "是否变频": "inverter",
    "制冷量": "cooling_capacity",
    "制冷功率": "cooling_power",
    "制热量": "heating_capacity",
    "制热功率": "heating_power",
    "循环风量": "air_flow",
    "室内机噪音": "indoor_noise",
    "室外机噪音": "outdoor_noise",
    "能效等级": "energy_grade",
    "全年能源消耗率(APF)": "apf",
    "制冷剂": "refrigerant",
    "电源性能": "power_spec",
    "机身颜色": "color",
    "室内机尺寸": "indoor_size",
    "室外机尺寸": "outdoor_size",
    "室内机质量": "indoor_weight",
    "室外机质量": "outdoor_weight",
}

# 列表页参数名 → 统一 schema 字段（详情页失败时的 fallback）
LIST_SPEC_FIELD_MAP = {
    "空调类型": "ac_type",
    "适用面积": "area",
    "产品功率": "hp",
    "功率": "hp_class",
    "冷暖类型": "cool_heat_type",
    "制冷量": "cooling_capacity",
    "制热量": "heating_capacity",
}


def parse_ac_type(value: str) -> str:
    text = clean_text(value)
    if "中央空调" in text or "风管机" in text:
        return "中央空调"
    if "柜" in text or "立式" in text or "圆柱" in text:
        return "立柜式"
    if "挂" in text or "壁挂" in text or "吸顶" in text:
        return "壁挂式"
    if "移动" in text:
        return "移动空调"
    return text or ""


def parse_inverter(value: str) -> bool | None:
    text = clean_text(value)
    if "变频" in text:
        return True
    if "定频" in text:
        return False
    return None


def parse_capacity_watts(value: str) -> float | None:
    """'3510(150-5250)W' -> 3510.0 ; '730m3/h' -> 730.0"""
    text = clean_text(value)
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_launch_date(value: str) -> str:
    """'2025年,3月' -> '2025-03'; '2025年' -> '2025'"""
    text = clean_text(value)
    match = re.search(r"(\d{4})年(?:[,\s，]*(\d{1,2}))?月?", text)
    if not match:
        return text
    year = match.group(1)
    month = match.group(2)
    return f"{year}-{int(month):02d}" if month else year


def parse_weight_kg(value: str) -> float | None:
    text = clean_text(value)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|KG|千克)", text)
    return float(match.group(1)) if match else None


def parse_apf(value: str) -> float | None:
    return parse_number(value)


def normalize_specs(specs: dict[str, str]) -> dict[str, Any]:
    """Map raw PConline spec names onto the unified AC schema."""
    out: dict[str, Any] = {}
    for raw_name, raw_value in specs.items():
        field = SPEC_FIELD_MAP.get(raw_name)
        if not field:
            continue
        value = clean_text(raw_value)
        if not value:
            continue
        if field == "ac_type":
            out[field] = parse_ac_type(value)
        elif field == "inverter":
            out[field] = parse_inverter(value)
        elif field in ("cooling_capacity", "cooling_power", "heating_capacity",
                       "heating_power", "air_flow"):
            out[field] = parse_capacity_watts(value)
        elif field == "launch_date":
            out[field] = parse_launch_date(value)
        elif field in ("indoor_weight", "outdoor_weight"):
            out[field] = parse_weight_kg(value)
        elif field == "apf":
            out[field] = parse_apf(value)
        else:
            out[field] = value
        out[f"{field}_raw"] = value
    return out


def parse_detail_specs(html: Any) -> dict[str, str]:
    """All th/td rows across every table.dtparams-table (basic/tech/other)."""
    specs: dict[str, str] = {}
    for table in html.select("table.dtparams-table"):
        for row in table.select("tr"):
            cells = row.select("th, td")
            if len(cells) < 2:
                continue
            key = clean_text(cells[0].get_text(" ", strip=True))
            value = clean_text(cells[1].get_text(" ", strip=True))
            # 去掉链接/悬浮提示噪音："2级 • 什么是能效等级 • 查看所有2级华凌"
            value = re.sub(r"\s*•\s*.*$", "", value)
            value = re.sub(r"(更多.*|进入官网.*)$", "", value).strip()
            if key and value and len(key) <= 24:
                specs[key] = value
    return specs


def parse_list_specs(card: Any) -> dict[str, str]:
    """Spec lines inside the list card (item-specs)."""
    specs: dict[str, str] = {}
    for row in card.select("ul.item-specs li"):
        label = row.select_one("span")
        value = row.select_one("em")
        if not label or not value:
            continue
        key = clean_text(label.get_text(" ", strip=True)).rstrip("：:")
        val = clean_text(value.get_text(" ", strip=True))
        if key and val:
            specs[key] = val
    return specs


def parse_list_page(html: Any, page: int, brand: str, brand_name: str,
                    session: Any, rotator: Any, delay: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, card in enumerate(html.select(LIST_SELECTOR), start=1):
        # 标题链接优先（带 title 文本）；图片链接在 DOM 中先出现但无标题
        title_link = card.select_one(".item-title-name") or card.select_one(
            "h3 a[href]"
        )
        link = title_link or card.select_one("a.pic[href]") or card.select_one(
            "a[href]"
        )
        if not link:
            continue
        href = link.get("href", "")
        if "air_condition/" not in href:
            continue
        pid_match = re.search(r"/(\d+)\.html(?:[#?]|$)", href)
        if not pid_match:
            continue
        product_id = pid_match.group(1)
        title = clean_text(
            link.get("title") or link.get_text(" ", strip=True)
        )
        if not title:
            continue
        rank = (page - 1) * 24 + index  # 24 cards per page
        price_node = card.select_one(".item-price") or card.select_one(
            ".price"
        )
        price = parse_price(
            price_node.get_text(" ", strip=True) if price_node else ""
        )
        list_specs = parse_list_specs(card)
        mapped: dict[str, Any] = {}
        for raw_name, raw_value in list_specs.items():
            field = LIST_SPEC_FIELD_MAP.get(raw_name)
            if not field:
                continue
            value = clean_text(raw_value)
            if field == "ac_type":
                mapped[field] = parse_ac_type(value)
            elif field in ("cooling_capacity", "heating_capacity"):
                mapped[field] = parse_capacity_watts(value)
            else:
                mapped[field] = value
        results.append(
            {
                "title": title,
                "model": title,
                "brand": brand_name,
                "brand_slug": brand,
                "price": price,
                "currency": "CNY",
                "source": "PConline",
                "atomic_source_names": ["PConline"],
                "source_category": "PConline air_condition popularity ranking",
                "source_rank": rank,
                "source_product_id": product_id,
                "source_url": absolute_url(BASE_URL, href),
                **mapped,
            }
        )
    return results


def enrich_item(session: Any, rotator: Any, item: dict[str, Any],
                delay: float) -> dict[str, Any]:
    brand = item.get("brand_slug", "")
    product_id = item.get("source_product_id", "")
    if not brand or not product_id:
        return item
    detail_url = f"{BASE_URL}/air_condition/{brand}/{product_id}_detail.html"
    try:
        if rotator and rotator.enabled:
            node = rotator.rotate()
        else:
            node = None
        detail, _final_url = get_html(
            session, detail_url, encoding="gb18030", delay=delay
        )
        if rotator and rotator.enabled and node:
            rotator.mark_success(node)
        specs = parse_detail_specs(detail)
        mapped = normalize_specs(specs)
        if mapped.get("model") and mapped["model"] != item.get("model"):
            item["model"] = mapped["model"]
            item["title"] = f"{item.get('brand', '')}{mapped['model']}"
        for key, value in mapped.items():
            item[key] = value
        item["detail_url"] = detail_url
        item["evidence"] = {"detail_specs": detail_url}
    except Exception as exc:
        if rotator and rotator.enabled and node:
            blocked = _looks_blocked(str(exc))
            rotator.mark_failure(node, blocked=blocked)
        print(f"enrich failed {product_id}: {type(exc).__name__}: {exc}")
    return item


def _looks_blocked(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("503", "429", "403", "checking", "risk", "captcha")
    )


def crawl_brand(session: Any, rotator: Any, brand: str, brand_name: str,
                progress: Progress, budget: Budget, delay: float,
                max_pages: int) -> list[dict[str, Any]]:
    """Crawl one brand's most-popular catalogue; returns new/updated items."""
    items: list[dict[str, Any]] = []
    page = progress.current_page
    while not budget.expired():
        # max_pages=0 表示不限制（0 值语义：与 laptops PConline 教训一致）
        if max_pages and page > max_pages:
            break
        url = f"{CATALOGUE_URL.format(brand=brand)}list.shtml" if page == 1 else _page_url(brand, page)
        try:
            if rotator and rotator.enabled:
                node = rotator.rotate()
            else:
                node = None
            html, final_url = get_html(
                session, url, encoding="gb18030", delay=delay
            )
            if rotator and rotator.enabled and node:
                rotator.mark_success(node)
        except Exception as exc:
            if rotator and rotator.enabled and node:
                rotator.mark_failure(node, blocked=_looks_blocked(str(exc)))
            print(f"brand {brand} page {page} failed: {type(exc).__name__}: {exc}")
            break
        page_items = parse_list_page(html, page, brand, brand_name, session,
                                     rotator, delay)
        if not page_items:
            # 最后翻页失败或已到尽头：视为扫描完成
            progress.scan_complete = True
            break
        items, added = merge_new_items(items, page_items, item_key)
        if added:
            print(f"brand {brand} page {page}: +{added} items (total {len(items)})")
        progress.current_page = page + 1
        progress.total_items = len(items)
        progress.save(Path("crawl_state/pconline"))
        next_link = html.select_one(NEXT_PAGE_SELECTOR)
        if not next_link or not next_link.get("href"):
            progress.scan_complete = True
            break
        page += 1
    return items


def _page_url(brand: str, page: int) -> str:
    # 第2页为 list_25s1.shtml，第3页为 list_35s1.shtml（已验证第2页 href）
    return f"{BASE_URL}/air_condition/{brand}/list_{page}5s1.shtml"


def load_brands(path: str | None = None) -> list[dict[str, str]]:
    if not path:
        path = os.path.join(os.path.dirname(__file__), "..", "config", "brands.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [{"slug": str(item.get("slug", "")), "name": str(item.get("name", ""))}
                for item in data if item.get("slug")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="PConline AC crawler")
    parser.add_argument("--output", required=True, help="output artifact json")
    parser.add_argument("--brands", default=None, help="brands.json path")
    parser.add_argument("--brand", action="append", default=None,
                        help="limit to specific brand slug(s)")
    parser.add_argument("--time-budget", type=int, default=0,
                        help="wall-clock budget in seconds (0=unlimited)")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--min-records", type=int, default=50)
    parser.add_argument("--progress-dir", default="crawl_state/pconline")
    args = parser.parse_args()

    brands = load_brands(args.brands)
    if args.brand:
        wanted = set(args.brand)
        brands = [b for b in brands if b["slug"] in wanted]
    if not brands:
        print("no brands configured")
        return 2

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())
    progress = Progress.load(Path(args.progress_dir))
    budget = Budget(args.time_budget)
    delay = human_delay(args.delay)

    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in read_jsonl(Path(args.progress_dir) / "enriched.jsonl"):
        key = item_key(line)
        if key and key not in seen:
            seen.add(key)
            all_items.append(line)

    for brand_cfg in brands:
        if budget.expired():
            print("time budget expired; stopping brand loop")
            break
        slug, name = brand_cfg["slug"], brand_cfg["name"]
        print(f"=== brand: {name} ({slug}) ===")
        brand_progress = Progress.load(Path(args.progress_dir))
        brand_progress.current_page = 1
        new_items = crawl_brand(
            session, rotator, slug, name, brand_progress, budget,
            delay, args.max_pages
        )
        for item in new_items:
            key = item_key(item)
            if key and key not in seen:
                seen.add(key)
                all_items.append(item)
                append_jsonl(Path(args.progress_dir) / "items.jsonl", item)

    # Enrichment pass (skips already-processed ids)
    processed = set(progress.processed_ids)
    for item in all_items:
        if budget.expired():
            break
        product_id = str(item.get("source_product_id", ""))
        if product_id and product_id in processed:
            continue
        item = enrich_item(session, rotator, item, delay)
        if product_id:
            processed.add(product_id)
            progress.processed_ids = sorted(processed)
            progress.save(Path(args.progress_dir))
        append_jsonl(Path(args.progress_dir) / "enriched.jsonl", item)

    # Final artifact
    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "source": "PConline",
        "brands": [b["slug"] for b in brands],
        "count": len(all_items),
        "items": all_items,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"artifact written: {out} ({len(all_items)} items)")
    if rotator.enabled:
        rotator.save_stats()
    if len(all_items) < args.min_records:
        print(f"FAIL: only {len(all_items)} records (< {args.min_records})")
        return 1
    if budget.expired():
        print("PARTIAL: time budget expired; progress saved for resume (exit 10)")
        return 10
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
