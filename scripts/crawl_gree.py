#!/usr/bin/env python3
"""格力官网爬虫（Playwright 渲染——官网 SPA + 证书异常需忽略）。

列表页 https://www.gree.com.cn/cmsProduct/list/41 （家用空调，view/{id} 产品）
详情页：点击"功能参数"tab → 型号列表（KFR）→ 逐型号点击 → 规格文本
（产品型号/APF(GB21455-2019)/能效等级/匹数/制冷量/制热量/功率/循环风量/噪音）。

数据：官方一手参数（APF 官方值——发布门禁关键字段）。
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

LIST_URL = "https://www.gree.com.cn/cmsProduct/list/41"
VIEW_URL = "https://www.gree.com.cn/cmsProduct/view/{pid}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def fetch_list_links(page: Any) -> list[str]:
    """列表页 view 链接（去重）。"""
    links = page.eval_on_selector_all(
        "a[href*='cmsProduct/view']",
        "els => [...new Set(els.map(e => e.href))]")
    return [l for l in links if "/view/" in l]


def extract_models(page: Any) -> list[str]:
    """详情页型号列表（KFR 开头元素）。"""
    models = page.eval_on_selector_all(
        "a, span, div, li",
        "els => els.map(e => (e.textContent||'').trim()).filter(t => /^KFR/.test(t))")
    return list(dict.fromkeys(models))


def extract_spec_text(page: Any) -> str:
    """当前型号的规格文本（产品型号：... 起始段）。"""
    return page.evaluate("""() => {
        const body = document.body.innerText;
        const idx = body.indexOf('产品型号');
        return idx >= 0 ? body.slice(idx, idx + 1200) : '';
    }""")


def parse_specs(spec_text: str) -> dict[str, str]:
    """规格文本 'key：value' 行 → dict。"""
    specs: dict[str, str] = {}
    for line in spec_text.splitlines():
        m = re.match(r"^(.+?)[：:]\s*(.+)$", line.strip())
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key and val:
                specs[key] = val
    return specs


def map_item(specs: dict[str, str], model: str) -> dict[str, Any]:
    """格力规格 → 统一字段。"""
    item: dict[str, Any] = {
        "title": model,
        "model": model,
        "brand": "格力",
        "source": "Gree",
        "atomic_source_names": ["Gree"],
        "source_category": "Gree official site",
        "currency": "CNY",
    }
    ptype = specs.get("产品类型", "")
    if "柜" in ptype or "立式" in ptype:
        item["ac_type"] = "立柜式"
    elif "挂" in ptype:
        item["ac_type"] = "壁挂式"
    hp = specs.get("匹数", "")
    if hp:
        item["hp"] = hp
    eg = specs.get("能效等级", "")
    if eg:
        item["energy_grade"] = eg
    inv = specs.get("变频/定频", "")
    if "变频" in inv:
        item["inverter"] = True
    elif "定频" in inv:
        item["inverter"] = False
    apf = specs.get("APF(GB21455-2019)", "") or specs.get("APF", "")
    if apf:
        item["apf"] = apf
    for src_key, dst_key in (
        ("额定制冷量(W)", "cooling_capacity"), ("额定制热量(W)", "heating_capacity"),
        ("额定制冷功率(W)", "cooling_power"), ("额定制热功率(W)", "heating_power"),
        ("循环风量(m³/h)", "air_flow"),
    ):
        val = specs.get(src_key)
        if val:
            num = parse_number(val)
            if num:
                item[dst_key] = num
    noise = specs.get("内机噪音dB(A)(静音档-高档)", "") or specs.get("内机噪音", "")
    if noise:
        item["indoor_noise"] = noise
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="Gree official site crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--min-records", type=int, default=5)
    parser.add_argument("--max-detail", type=int, default=0,
                        help="max products to fetch (0=all)")
    parser.add_argument("--proxy", default=None,
                        help="proxy URL for playwright (default: HTTP_PROXY env)")
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
            user_agent=UA, locale="zh-CN", ignore_https_errors=True,
            proxy={"server": proxy} if proxy else None)
        page = ctx.new_page()

        # 1) 列表
        page.goto(LIST_URL, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        links = fetch_list_links(page)
        print(f"列表页产品链接: {len(links)}")
        if args.max_detail:
            links = links[:args.max_detail]

        # 2) 详情（逐产品）
        for idx, url in enumerate(links):
            if budget.expired():
                print("time budget expired, stop")
                break
            pid = url.rstrip("/").split("/")[-1]
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                # 点功能参数 tab
                clicked = False
                for txt in ("功能参数", "参数"):
                    try:
                        page.get_by_text(txt, exact=False).first.click(timeout=4000)
                        page.wait_for_timeout(2500)
                        clicked = True
                        break
                    except Exception:
                        continue
                if not clicked:
                    print(f"  {pid}: 无参数 tab")
                    continue
                models = extract_models(page)
                if not models:
                    print(f"  {pid}: 无型号")
                    continue
                # 逐型号点击提取规格（绑定校验：规格的产品型号必须=目标型号）
                for model in models:
                    if budget.expired():
                        break
                    clean_model = re.sub(r"\s+", "", model)
                    if clean_model in seen:
                        continue
                    # 点击目标型号（若当前规格不是该型号）
                    for attempt in range(2):
                        try:
                            page.get_by_text(model, exact=True).first.click(timeout=4000)
                            page.wait_for_timeout(1800)
                        except Exception:
                            pass
                        spec_text = extract_spec_text(page)
                        specs = parse_specs(spec_text)
                        bound = re.sub(r"\s+", "", specs.get("产品型号", ""))
                        if bound == clean_model:
                            break
                    if re.sub(r"\s+", "", specs.get("产品型号", "")) != clean_model:
                        print(f"  {clean_model}: 规格绑定失败，跳过（防错绑）")
                        continue
                    item = map_item(specs, clean_model)
                    seen.add(clean_model)
                    item["source_url"] = url
                    all_items.append(item)
                    print(f"  + {clean_model} (APF={specs.get('APF(GB21455-2019)','?')} "
                          f"{specs.get('匹数','?')} 能效{specs.get('能效等级','?')})")
            except Exception as exc:
                print(f"  {pid} failed: {type(exc).__name__}: {str(exc)[:100]}")
        browser.close()

    payload = {
        "schema_version": "1.0",
        "source": "Gree",
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
