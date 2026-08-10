#!/usr/bin/env python3
"""Crawl ZOL air-conditioner catalogue (server-rendered, most-popular order).

Note: ZOL currently answers data-centre / foreign IPs with a ``checking``
anti-bot page.  This crawler MUST run behind the airport-subscription proxy
with per-request node rotation (scripts/node_rotator.py).  The exact AC
catalogue path mirrors the notebook pattern that crawl_laptops uses
(``{catalogue}_index/subcate{id}_0_list_1_0_1_2_0_{page}.html``); the AC
subcategory id is resolved at first proxy-enabled run by probing candidate
paths and picking the one that renders product cards (``#J_PicMode``).
Until the id is confirmed the crawler fails loudly instead of emitting
verification-page HTML as data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

try:
    from scripts.crawler_utils import (
        absolute_url,
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
        absolute_url,
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

BASE_URL = "https://detail.zol.com.cn"
# ZOL 空调分类真实路径（2026-08-10 Playwright 实测：air-condition/ 直连可达 61 卡；
# aircon/ 是 404 + checking；subcate 模板 URL 概率 checking）
CATALOGUE = "air-condition"
SUB_CATEGORY_ID = 345
LIST_PAGE_URL = f"{BASE_URL}/{CATALOGUE}/{{page}}.html"
LIST_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def is_checking_page(html: Any, final_url: str) -> bool:
    text = clean_text(html.get_text(" ", strip=True))[:400]
    return "checking" in final_url or "安全验证" in text or "访问异常" in text


def parse_ranking_page(html: Any, page: int, brand: str | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, card in enumerate(
        html.select("#J_PicMode > li[data-follow-id]"), start=1
    ):
        link = card.select_one("h3 .title-black a[href]") or card.select_one(
            "a.pic[href]"
        )
        if not link:
            continue
        title = clean_text(link.get_text(" ", strip=True) or link.get("title"))
        if not title:
            # a.pic 里是 img——标题在 img alt
            img = card.select_one("img[alt]")
            if img:
                title = clean_text(img.get("alt"))
        if not title:
            continue
        rank = (page - 1) * 48 + index
        price_node = card.select_one(".price-type") or card.select_one(".price")
        product_id = clean_text(card.get("data-follow-id")).lstrip("p")
        results.append(
            {
                "title": title,
                "model": title,
                "brand": brand or infer_brand(title),
                "price": parse_price(
                    price_node.get_text(" ", strip=True) if price_node else ""
                ),
                "currency": "CNY",
                "source": "ZOL",
                "atomic_source_names": ["ZOL"],
                "source_category": "ZOL air_condition ranking",
                "source_rank": rank,
                "source_product_id": product_id,
                "source_url": absolute_url(BASE_URL, link.get("href", "")),
            }
        )
    return results


def infer_brand(title: str) -> str:
    text = clean_text(title)
    for name in ("格力", "美的", "海尔", "奥克斯", "TCL", "海信", "科龙",
                 "长虹", "华凌", "小米", "三菱", "大金", "松下", "志高", "创维"):
        if name in text:
            return name
    return clean_text(title).split(" ", 1)[0][:24]


def resolve_catalogue(session: Any, rotator: Any, delay: float) -> str:
    """ZOL 空调分类路径已实测确认（air-condition/，2026-08-10 Playwright）。
    返回 CATALOGUE；调用方负责运行时 checking 重试。"""
    return CATALOGUE


def fetch_list_page_playwright(page_num: int, proxy: str | None = None) -> str | None:
    """Playwright 渲染列表页（air-condition/{page}.html）。

    ZOL checking 挑战是概率性的（同 URL 有时放行有时拦，实测 3.html 放行、
    2.html 被拦）——渲染后若落到 checking 页，点击"继续访问"按钮并重试。
    返回渲染后的完整 HTML；3 次尝试均失败返回 None。
    """
    from playwright.sync_api import sync_playwright

    url = LIST_PAGE_URL.format(page=page_num)
    launch_args = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    for attempt in range(1, 5):
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_args)
            ctx = browser.new_context(
                user_agent=LIST_UA,
                locale="zh-CN",
                proxy={"server": proxy} if proxy else None,
            )
            page = ctx.new_page()
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if "checking" in page.url:
                    for btn in page.locator("a, button").all():
                        if "继续" in (btn.inner_text() or ""):
                            try:
                                btn.click(timeout=5000)
                                page.wait_for_timeout(6000)
                            except Exception:
                                pass
                            break
                # 判定：checking 页特征（URL 含 checking 或正文含"防刷"）
                body_text = page.inner_text("body")
                if "checking" not in page.url and "防刷" not in body_text:
                    return page.content()
                print(f"  list page {page_num} attempt {attempt}: checking, retry")
            except Exception as exc:
                print(f"  list page {page_num} attempt {attempt}: {type(exc).__name__}")
            finally:
                browser.close()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="ZOL AC crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="max list pages to scan (0=unlimited)")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--min-records", type=int, default=50)
    parser.add_argument("--progress-dir", default="crawl_state/zol")
    args = parser.parse_args()

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())
    if not rotator.enabled:
        print("FAIL: ZOL requires proxy node rotation (checking anti-bot)")
        return 2

    catalogue = resolve_catalogue(session, rotator, args.delay)
    if not catalogue:
        print("FAIL: could not resolve ZOL AC catalogue behind proxy")
        return 2
    sub_id = SUB_CATEGORY_ID

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

    page = progress.current_page
    proxy = None
    if rotator and rotator.enabled:
        node = rotator.rotate()
        # setup_proxy_runtime 导出的 HTTP_PROXY=http://127.0.0.1:7890
        proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "http://127.0.0.1:7890"
    while not budget.expired():
        # max_pages=0 表示不限制（0 值语义）
        if args.max_pages and page > args.max_pages:
            break
        content = fetch_list_page_playwright(page, proxy)
        if content is None:
            print(f"ZOL page {page} blocked after retries; aborting scan")
            if rotator and rotator.enabled:
                rotator.mark_failure(node, blocked=True)
            break
        html = BeautifulSoup(content, "html.parser")
        if is_checking_page(html, ""):
            print(f"ZOL page {page} returned checking page; aborting scan")
            if rotator and rotator.enabled:
                rotator.mark_failure(node, blocked=True)
            break
        if rotator and rotator.enabled:
            rotator.mark_success(node)
        page_items = parse_ranking_page(html, page)
        if not page_items:
            progress.scan_complete = True
            break
        merged, added = merge_new_items(all_items, page_items, item_key)
        all_items = merged
        if added:
            print(f"ZOL page {page}: +{added} (total {len(all_items)})")
        progress.current_page = page + 1
        progress.total_items = len(all_items)
        progress.save(Path(args.progress_dir))
        page += 1

    # Enrichment from param pages (product bucket = id // 1000)
    processed = set(progress.processed_ids)
    for item in all_items:
        if budget.expired():
            break
        product_id = str(item.get("source_product_id", ""))
        if product_id and product_id in processed:
            continue
        if product_id:
            bucket = (int(product_id) + 999) // 1000
            param_url = f"{BASE_URL}/{bucket}/{product_id}/param.shtml"
            try:
                if rotator and rotator.enabled:
                    node = rotator.rotate()
                detail, _ = get_html(session, param_url, encoding="gb18030",
                                     delay=delay)
                if rotator and rotator.enabled and node:
                    rotator.mark_success(node)
                if not is_checking_page(detail, _):
                    specs: dict[str, str] = {}
                    for row in detail.select("tr"):
                        cells = row.select("th, td")
                        if len(cells) < 2:
                            continue
                        key = clean_text(cells[0].get_text(" ", strip=True)).replace("纠错", "")
                        value = clean_text(cells[1].get_text(" ", strip=True)).replace("纠错", "")
                        if key and value and len(key) <= 24:
                            specs[key] = value
                    item["zol_specs"] = specs
                    item["detail_url"] = param_url
            except Exception as exc:
                if rotator and rotator.enabled and node:
                    rotator.mark_failure(node, blocked=True)
                print(f"ZOL enrich failed {product_id}: {type(exc).__name__}")
        processed.add(product_id)
        progress.processed_ids = sorted(processed)
        progress.save(Path(args.progress_dir))
        append_jsonl(Path(args.progress_dir) / "enriched.jsonl", item)

    payload = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "source": "ZOL",
        "catalogue": catalogue,
        "sub_category_id": sub_id,
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
