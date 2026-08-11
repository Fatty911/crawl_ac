#!/usr/bin/env python3
"""美的商城（midea.cn SPA）爬虫——覆盖美的 + 华凌（美的子品牌）。

搜索页 https://www.midea.cn/#/search?keyword=XXX（Playwright 渲染 SPA）
商品卡标题含型号/匹数/能效/类型/价格；详情页含 APF/制冷量（后续可补充）。
过滤：无 KFR 型号的配件（滤芯/遥控器）剔除。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.crawler_utils import parse_number
    from scripts.crawl_runtime import Budget, human_delay
except ModuleNotFoundError:
    from crawler_utils import parse_number
    from crawl_runtime import Budget, human_delay

SEARCH_URL = "https://www.midea.cn/#/search?keyword={kw}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

KEYWORDS = ["空调", "华凌空调", "美的空调"]
MAX_PAGES_PER_KW = 3

MODEL_RE = re.compile(
    r"(KFRD?|KFD?|KF)\s*[-－]?\s*(\d{2,3})\s*([A-Z]{1,6})?\s*/\s*([0-9A-Za-zⅢⅣⅠⅡ()\-]{2,30})",
    re.IGNORECASE)
HP_RE = re.compile(r"(大|小)?\s*(\d+(?:\.\d+)?)\s*[P匹]")
ENERGY_RE = re.compile(r"(新一级能效|新一级|一级能效|二级能效|三级能效|1级|2级|3级)")
TYPE_RE = re.compile(r"(壁挂式|立柜式|柜机|挂机|空调柜机|空调挂机)")
BRAND_HINTS = [("华凌", "华凌"), ("美的", "美的"), ("COLMO", "COLMO"), ("小天鹅", "小天鹅")]


def parse_card(text: str) -> dict[str, Any] | None:
    """商品卡文本 → item（无 KFR 型号返回 None——配件过滤）。"""
    model_m = MODEL_RE.search(text)
    if not model_m:
        return None
    model = model_m.group(0).upper().replace(" ", "")
    brand = "未知"
    for name, b in BRAND_HINTS:
        if name in text:
            brand = b
            break
    item: dict[str, Any] = {
        "title": text[:120],
        "model": model,
        "brand": brand,
        "source": "MideaMall",
        "atomic_source_names": ["MideaMall"],
        "source_category": "Midea official mall",
        "currency": "CNY",
    }
    hp_m = HP_RE.search(text)
    if hp_m:
        prefix = hp_m.group(1) or ""
        item["hp"] = f"{prefix}{hp_m.group(2)}匹"
    eg_m = ENERGY_RE.search(text)
    if eg_m:
        item["energy_grade"] = eg_m.group(1)
    type_m = TYPE_RE.search(text)
    if type_m:
        t = type_m.group(1)
        item["ac_type"] = "壁挂式" if ("挂" in t) else "立柜式"
    if "变频" in text:
        item["inverter"] = True
    price_m = re.search(r"¥\s*([\d,]+\.?\d*)", text)
    if price_m:
        price = float(price_m.group(1).replace(",", ""))
        # 价格门禁：<300 是配件/安装费（空调本体价远高于此）
        if price < 300:
            return None
        item["price"] = price
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="Midea mall crawler (Midea + Hualing)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--min-records", type=int, default=5)
    parser.add_argument("--proxy", default=None)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    budget = Budget(args.time_budget)
    proxy = args.proxy or None
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA, locale="zh-CN",
            proxy={"server": proxy} if proxy else None)
        page = ctx.new_page()

        for kw in KEYWORDS:
            if budget.expired():
                break
            for page_num in range(1, MAX_PAGES_PER_KW + 1):
                if budget.expired():
                    break
                url = SEARCH_URL.format(kw=kw)
                if page_num > 1:
                    url += f"&page={page_num}"
                try:
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5000)
                except Exception as exc:
                    print(f"kw={kw} p{page_num} failed: {type(exc).__name__}")
                    continue
                # 商品卡文本（含 KFR 的短块）
                cards = page.eval_on_selector_all(
                    "div, li", """els => els.filter(e => {
                        const t = e.textContent || '';
                        return t.includes('KFR') && t.length > 60 && t.length < 400;
                    }).map(e => (e.textContent || '').trim().replace(/\\s+/g, ' '))""")
                before = len(all_items)
                for card_text in cards:
                    if budget.expired():
                        break
                    item = parse_card(card_text)
                    if not item:
                        continue
                    key = item["model"] + "|" + str(item.get("price", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    all_items.append(item)
                print(f"kw={kw} p{page_num}: 卡={len(cards)} 新增={len(all_items)-before} (总 {len(all_items)})")
                # 分页：无新卡则停
                if len(cards) == 0:
                    break
        browser.close()

    payload = {
        "schema_version": "1.0",
        "source": "MideaMall",
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
