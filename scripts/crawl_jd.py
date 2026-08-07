#!/usr/bin/env python3
"""Crawl JD air-conditioner 15-day sales ranking (server-rendered hotitem).

JD's React search/list pages force anonymous logins, so this crawler uses the
official server-rendered hotitem ranking page with
``sort_type=sort_totalsales15_desc`` (the same pattern proven in
crawl_laptops).  The AC ranking URL is category-specific and resolved at
first proxy-enabled run: we probe the JD AC channel navigation for the
"空调排行榜" hotitem link and persist it in
``crawl_state/jd/hotitem_url.txt``.  If the ranking cannot be reached the
crawler fails loudly; it never emits risk-verification HTML as data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

try:
    from scripts.crawler_utils import (
        clean_text,
        get_html,
        make_session,
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
        clean_text,
        get_html,
        make_session,
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

JD_HOME = "https://www.jd.com/"
HOTITEM_STATE = "crawl_state/jd/hotitem_url.txt"


def is_risk_page(html: Any, final_url: str) -> bool:
    title = clean_text(html.title.get_text() if html.title else "")
    text = clean_text(html.get_text(" ", strip=True))[:500]
    return (
        "risk_handler" in final_url
        or "passport.jd.com" in final_url
        or "京东安全" in title
        or "访问验证" in text
    )


def sales_url(hotitem: str, page: int) -> str:
    query = {
        "extAttrValue": "expand_name,",
        "electedExtAttrSet": "",
        "sort_type": "sort_totalsales15_desc",
        "page": str(page),
    }
    return f"{hotitem}?{urlencode(query)}"


def find_hotitem_url(session: Any, rotator: Any, delay: float) -> str:
    """Discover the JD AC sales-ranking hotitem URL from the AC channel."""
    candidates = [
        "https://www.jd.com/",
        "https://channel.jd.com/aircondition.html",
        "https://list.jd.com/list.html?cat=737,794,798",
    ]
    patterns = (
        re.compile(r"hotitem/[0-9a-f]+\.html"),
        re.compile(r'["\'](/hotitem/[0-9a-f]+\.html)["\']'),
        re.compile(r"空调排行榜[^<]{0,40}?href=[\"']([^\"']+)[\"']"),
    )
    for url in candidates:
        try:
            if rotator and rotator.enabled:
                node = rotator.rotate()
            html, final_url = get_html(session, url, encoding="utf-8",
                                       delay=delay)
            if rotator and rotator.enabled and node:
                rotator.mark_success(node)
        except Exception as exc:
            if rotator and rotator.enabled and node:
                rotator.mark_failure(node, blocked=True)
            print(f"hotitem probe failed {url}: {type(exc).__name__}")
            continue
        for pattern in patterns:
            for match in pattern.finditer(str(html)):
                raw = match.group(1) if match.groups() else match.group(0)
                if raw.startswith("//"):
                    raw = "https:" + raw
                elif raw.startswith("/"):
                    raw = "https://www.jd.com" + raw
                if "hotitem" in raw:
                    print(f"JD AC hotitem discovered: {raw}")
                    return raw
    return ""


def parse_search_page(html: Any, page: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, card in enumerate(html.select("li.sku-detail"), start=1):
        name_node = card.select_one(".p-name a[href]")
        title = clean_text(
            (name_node.get("title") if name_node else "")
            or (name_node.get_text(" ", strip=True) if name_node else "")
        )
        price_box = card.select_one(".p-price[data-skuid]")
        sku = clean_text(price_box.get("data-skuid") if price_box else "")
        if not sku and name_node:
            sku_match = re.search(
                r"/(\d+)\.html", urlparse(name_node.get("href", "")).path
            )
            sku = sku_match.group(1) if sku_match else ""
        if not sku or not title:
            continue
        price_node = card.select_one(".p-price strong")
        shop_node = card.select_one(".p-merchant")
        items.append(
            {
                "title": title,
                "model": title,
                "brand": infer_brand(title),
                "price": parse_price(
                    price_node.get_text(strip=True) if price_node else ""
                ),
                "currency": "CNY",
                "merchant": clean_text(
                    shop_node.get_text(" ", strip=True) if shop_node else ""
                ),
                "source": "JD",
                "atomic_source_names": ["JD"],
                "source_category": "JD air_condition 15-day sales ranking",
                "source_rank": (page - 1) * 60 + index,
                "source_product_id": sku,
                "source_url": f"https://item.jd.com/{sku}.html",
            }
        )
    return items


def infer_brand(title: str) -> str:
    text = clean_text(title)
    for name in ("格力", "美的", "海尔", "奥克斯", "TCL", "海信", "科龙",
                 "长虹", "华凌", "小米", "三菱", "大金", "松下", "志高", "创维"):
        if name in text:
            return name
    return clean_text(title).split(" ", 1)[0][:24]


def parse_product_specs(html: Any) -> dict[str, str]:
    specs: dict[str, str] = {}
    for item in html.select(".Ptable-item, .parameter2 li, ul.parameter2 li"):
        if item.name == "li":
            text = clean_text(item.get_text(" ", strip=True))
            if "：" in text or ":" in text:
                parts = re.split(r"[：:]", text, maxsplit=1)
                specs[clean_text(parts[0])] = clean_text(parts[1])
            continue
        for row in item.select("dl"):
            key_node = row.select_one("dt")
            value_node = row.select_one("dd")
            if key_node and value_node:
                specs[clean_text(key_node.get_text(" ", strip=True))] = clean_text(
                    value_node.get_text(" ", strip=True)
                )
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description="JD AC crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--hotitem", default=None,
                        help="JD hotitem ranking URL (auto-discovered if absent)")
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="max list pages to scan (0=unlimited)")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--min-records", type=int, default=50)
    parser.add_argument("--progress-dir", default="crawl_state/jd")
    args = parser.parse_args()

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())

    progress_dir = Path(args.progress_dir)
    hotitem = args.hotitem
    state_file = progress_dir / "hotitem_url.txt"
    if not hotitem and state_file.exists():
        hotitem = state_file.read_text(encoding="utf-8").strip()
    if not hotitem:
        if not rotator.enabled:
            print("FAIL: JD requires proxy node rotation; no hotitem URL known")
            return 2
        hotitem = find_hotitem_url(session, rotator, args.delay)
        if hotitem:
            progress_dir.mkdir(parents=True, exist_ok=True)
            state_file.write_text(hotitem + "\n", encoding="utf-8")
        else:
            print("FAIL: could not discover JD AC hotitem ranking URL")
            return 2

    progress = Progress.load(progress_dir)
    budget = Budget(args.time_budget)
    delay = human_delay(args.delay)
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in read_jsonl(progress_dir / "enriched.jsonl"):
        key = item_key(line)
        if key and key not in seen:
            seen.add(key)
            all_items.append(line)

    page = progress.current_page
    while not budget.expired():
        # max_pages=0 表示不限制（0 值语义）
        if args.max_pages and page > args.max_pages:
            break
        url = sales_url(hotitem, page)
        try:
            if rotator and rotator.enabled:
                node = rotator.rotate()
            html, final_url = get_html(session, url, encoding="utf-8",
                                       delay=delay)
            if rotator and rotator.enabled and node:
                rotator.mark_success(node)
        except Exception as exc:
            if rotator and rotator.enabled and node:
                rotator.mark_failure(node, blocked=True)
            print(f"JD page {page} failed: {type(exc).__name__}: {exc}")
            break
        if is_risk_page(html, final_url):
            print(f"JD page {page} returned risk verification; aborting scan")
            break
        page_items = parse_search_page(html, page)
        if not page_items:
            progress.scan_complete = True
            break
        merged, added = merge_new_items(all_items, page_items, item_key)
        all_items = merged
        if added:
            print(f"JD page {page}: +{added} (total {len(all_items)})")
        progress.current_page = page + 1
        progress.total_items = len(all_items)
        progress.save(progress_dir)
        page += 1

    # Enrich from item detail pages
    processed = set(progress.processed_ids)
    for item in all_items:
        if budget.expired():
            break
        sku = str(item.get("source_product_id", ""))
        if sku and sku in processed:
            continue
        if sku:
            try:
                if rotator and rotator.enabled:
                    node = rotator.rotate()
                detail, _ = get_html(session, item["source_url"],
                                     encoding="utf-8", delay=delay)
                if rotator and rotator.enabled and node:
                    rotator.mark_success(node)
                if not is_risk_page(detail, _):
                    specs = parse_product_specs(detail)
                    if specs:
                        item["jd_specs"] = specs
                        item["detail_url"] = item["source_url"]
            except Exception as exc:
                if rotator and rotator.enabled and node:
                    rotator.mark_failure(node, blocked=True)
                print(f"JD enrich failed {sku}: {type(exc).__name__}")
        processed.add(sku)
        progress.processed_ids = sorted(processed)
        progress.save(progress_dir)
        append_jsonl(progress_dir / "enriched.jsonl", item)

    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "source": "JD",
        "hotitem_url": hotitem,
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
