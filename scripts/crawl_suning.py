#!/usr/bin/env python3
"""苏宁易购空调爬虫（第二源，服务端渲染搜索页）。

搜索页 https://search.suning.com/空调/ 服务端渲染 30 卡/页（实测），
商品标题含型号（KFR-xxx）、匹数、能效等级、类型（壁挂式/立柜式）——
与 PConline 记录按型号 identity 合并，提升双源率。

数据说明：苏宁记录是合并素材（不独立过准入），字段尽可能从标题解析；
价格经 def-price 渲染（部分可见）。匿名无 cookie，节点轮换出站。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

try:
    from scripts.crawler_utils import (
        clean_text, get_html, make_session, parse_number,
    )
    from scripts.crawl_runtime import (
        Budget, Progress, human_delay, read_jsonl, rewrite_jsonl,
    )
except ModuleNotFoundError:
    from crawler_utils import (
        clean_text, get_html, make_session, parse_number,
    )
    from crawl_runtime import (
        Budget, Progress, human_delay, read_jsonl, rewrite_jsonl,
    )

try:
    from scripts.node_rotator import make_rotator
except ModuleNotFoundError:
    from node_rotator import make_rotator

SEARCH_URL = "https://search.suning.com/{keyword}/"
KEYWORD = "空调"
CARDS_SELECTOR = "div.product-box"
STATE_DIR = "crawl_state/suning"

MODEL_RE = re.compile(r"(KFRD?|KFD?|KF)\s*[-－]?\s*(\d{2,3})\s*([A-Z]{1,6})?\s*/?\s*([0-9A-Za-zⅢⅣ()\-]{2,30})", re.IGNORECASE)
HP_RE = re.compile(r"(大|小)?\s*(\d+(?:\.\d+)?)\s*[P匹]")
ENERGY_RE = re.compile(r"(新一级能效|新一级|一级能效|二级能效|三级能效|1级|2级|3级)")
TYPE_RE = re.compile(r"(壁挂式|立柜式|柜机|挂机|吸顶式|中央空调|风管机)")
BRAND_HINTS = [
    ("格力", "格力"), ("美的", "美的"), ("海尔", "海尔"), ("奥克斯", "奥克斯"),
    ("TCL", "TCL"), ("海信", "海信"), ("科龙", "科龙"), ("长虹", "长虹"),
    ("华凌", "华凌"), ("统帅", "统帅"), ("Leader", "统帅"), ("小米", "小米"),
    ("米家", "米家"), ("COLMO", "COLMO"), ("卡萨帝", "卡萨帝"), ("云米", "云米"),
    ("追觅", "追觅"), ("大金", "大金"), ("三菱", "三菱"), ("松下", "松下"),
    ("志高", "志高"), ("康佳", "康佳"), ("创维", "创维"), ("格兰仕", "格兰仕"),
]


def parse_brand(title: str) -> str:
    for hint, name in BRAND_HINTS:
        if hint in title:
            return name
    return ""


def parse_hp(text: str) -> str | None:
    match = HP_RE.search(text)
    if not match:
        return None
    prefix = match.group(1) or ""
    number = float(match.group(2))
    number_str = str(int(number)) if number == int(number) else str(number)
    return f"{prefix}{number_str}匹"


def parse_energy(text: str) -> str | None:
    match = ENERGY_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    if value in ("一级能效", "1级"):
        return "新一级" if "新" in text[max(0, match.start()-10):match.start()] else "1级"
    if value == "新一级能效":
        return "新一级"
    if value == "二级能效":
        return "2级"
    if value == "三级能效":
        return "3级"
    return value


def parse_ac_type(text: str) -> str | None:
    match = TYPE_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    if value in ("柜机",):
        return "立柜式"
    if value in ("挂机",):
        return "壁挂式"
    if value in ("中央空调", "风管机", "吸顶式"):
        return "中央空调"
    return value


def parse_card(card: Any, index: int, page: int) -> dict[str, Any] | None:
    link = card.select_one("a.sellPoint[href]") or card.select_one(
        ".title-selling-point a[href]") or card.select_one("a[href]")
    if not link:
        return None
    href = link.get("href", "")
    if "product.suning.com" not in href:
        return None
    m = re.search(r"/(\d+)\.html", href)
    if not m:
        return None
    product_id = m.group(1)
    title = clean_text(
        link.get("title") or link.get_text(" ", strip=True)
    )
    if not title:
        return None
    title_node = card.select_one(".title-selling-point a")
    full_title = clean_text(
        (title_node.get_text(" ", strip=True) if title_node else "") or title
    )
    price_node = card.select_one("span.def-price") or card.select_one(
        ".price-box")
    price = None
    if price_node:
        price = parse_number(price_node.get_text(" ", strip=True))
    model_match = MODEL_RE.search(full_title)
    model = model_match.group(0).upper().replace(" ", "") if model_match else None
    rank = (page - 1) * 30 + index
    return {
        "title": full_title,
        "model": model or full_title[:40],
        "brand": parse_brand(full_title),
        "price": price,
        "currency": "CNY",
        "source": "Suning",
        "atomic_source_names": ["Suning"],
        "source_category": "Suning air-conditioning search ranking",
        "source_rank": rank,
        "source_product_id": product_id,
        "source_url": "https:" + href if href.startswith("//") else href,
        "ac_type": parse_ac_type(full_title),
        "hp": parse_hp(full_title),
        "energy_grade": parse_energy(full_title),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Suning AC crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="max list pages to scan (0=unlimited)")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--min-records", type=int, default=50)
    parser.add_argument("--progress-dir", default=STATE_DIR)
    args = parser.parse_args()

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())

    progress_dir = Path(args.progress_dir)
    progress = Progress.load(progress_dir)
    budget = Budget(args.time_budget)
    delay = human_delay(args.delay)
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in read_jsonl(progress_dir / "enriched.jsonl"):
        key = line.get("source_product_id")
        if key and key not in seen:
            seen.add(key)
            all_items.append(line)

    page = progress.current_page or 1
    while not budget.expired():
        if args.max_pages and page > args.max_pages:
            break
        url = SEARCH_URL.format(keyword=urllib.parse.quote(KEYWORD)) + f"?pageNumber={page}"
        try:
            if rotator and rotator.enabled:
                node = rotator.rotate()
            html, final_url = get_html(session, url, encoding="gb18030",
                                       delay=delay)
            if rotator and rotator.enabled and node:
                rotator.mark_success(node)
        except Exception as exc:
            if rotator and rotator.enabled and node:
                rotator.mark_failure(node, blocked=True)
            print(f"page {page} failed: {type(exc).__name__}")
            break
        cards = html.select(CARDS_SELECTOR)
        if not cards:
            print(f"page {page}: no cards (end of list?)")
            break
        page_items = []
        for index, card in enumerate(cards, start=1):
            item = parse_card(card, index, page)
            if not item:
                continue
            key = item["source_product_id"]
            if key in seen:
                continue
            seen.add(key)
            page_items.append(item)
        print(f"page {page}: +{len(page_items)} items (total {len(all_items) + len(page_items)})")
        all_items.extend(page_items)
        progress.current_page = page + 1
        progress.save(progress_dir)
        page += 1

    payload = {
        "schema_version": "1.0",
        "source": "Suning",
        "scraped_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
        "items": all_items,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rewrite_jsonl(progress_dir / "items.jsonl", all_items)
    print(f"artifact written: {out} ({len(all_items)} items)")
    if len(all_items) < args.min_records:
        print(f"records {len(all_items)} < min {args.min_records}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
