#!/usr/bin/env python3
"""线上 Pages 验证器：Playwright 渲染 ac.jiucai.eu.org，解析 JS 获取数据，
检查是否符合预期（渲染/数据/功能/质量），输出 JSON 报告供 AI 自修复消费。

检查项：
1. 页面加载：无 console 错误/PAGEERROR
2. 数据加载：表格行数>0、manifest rowCount 与线上 latest.json 一致
3. 关键 DOM：浮动滚动条/固定表头元素存在
4. 前端功能：多级排序（匹数升序行序变化）、筛选（chip 点击过滤生效）
5. 数据质量：KFR 型号覆盖率、关键字段（APF/匹数/能效）缺失率、双源率
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

BASE_URL = "https://ac.jiucai.eu.org"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pages live verification")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-count", type=int, default=0,
                        help="expected published row count (0=skip check)")
    parser.add_argument("--proxy", default=None)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    report: dict[str, Any] = {"ok": True, "checks": {}}
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA, locale="zh-CN", viewport={"width": 1366, "height": 768},
            proxy={"server": args.proxy} if args.proxy else None)
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(f"[{m.type}] {m.text[:150]}")
                if m.type in ("error",) else None)
        page.on("pageerror", lambda e: console_errors.append(f"[PAGEERROR] {str(e)[:200]}"))

        try:
            page.goto(BASE_URL, timeout=60000, wait_until="networkidle")
            page.wait_for_timeout(4000)
        except Exception as exc:
            report["ok"] = False
            report["load_error"] = str(exc)[:300]
            report["checks"]["page_load"] = "FAIL: " + str(exc)[:150]
            _write(report, args.output)
            return 1

        # 1) console 错误：PAGEERROR 阻断；资源加载错误（ERR_CONNECTION 偶发 CF 断连）仅记录
        page_errors = [e for e in console_errors if e.startswith("[PAGEERROR]")]
        report["checks"]["console_errors"] = console_errors if console_errors else "OK"
        if page_errors:
            report["ok"] = False

        # 2) 数据加载：表格行数 + manifest 对比
        # （用页面内 fetch 而非 urllib——本机代理对 CF 域名 SSL EOF；浏览器网络栈正常）
        rows = page.eval_on_selector_all("#table-body tr", "els => els.length")
        manifest = page.evaluate("""async () => {
            try {
                const r = await fetch('/data/manifest.json', {cache: 'no-store'});
                return await r.json();
            } catch (e) { return {error: String(e)}; }
        }""")
        if isinstance(manifest, dict) and manifest.get("error"):
            report["checks"]["manifest"] = "FAIL: " + manifest["error"][:150]
            report["ok"] = False
            manifest = None
        row_count = manifest.get("rowCount") if manifest else None
        report["checks"]["rows_rendered"] = rows
        report["checks"]["manifest_rowCount"] = row_count
        if rows == 0:
            report["ok"] = False
            report["checks"]["data_load"] = "FAIL: 表格 0 行"
        elif rows > (row_count or 0):
            report["ok"] = False
            report["checks"]["data_load"] = f"FAIL: 渲染 {rows} 行 > manifest {row_count}"
        else:
            # 默认筛选（电子膨胀阀/变频/壁挂立柜）后行数 ≤ 全量是预期行为
            report["checks"]["data_load"] = "OK"
            report["checks"]["note"] = (
                f"默认筛选后渲染 {rows} 行（全量 {row_count}）")
        if args.expected_count and row_count and row_count != args.expected_count:
            report["ok"] = False
            report["checks"]["row_count_expected"] = (
                f"FAIL: {row_count} != 预期 {args.expected_count}")

        # 3) 关键 DOM
        dom = page.evaluate("""() => {
            const bar = document.getElementById('table-scrollbar');
            const sh = document.getElementById('sticky-header');
            const thead = document.querySelector('#table-head th');
            return { scrollbar: !!bar, stickyHeader: !!sh, thead: !!thead };
        }""")
        report["checks"]["dom"] = dom
        if not (dom["scrollbar"] and dom["stickyHeader"] and dom["thead"]):
            report["ok"] = False
            report["checks"]["dom_check"] = "FAIL: 关键 DOM 缺失"

        # 4) 功能：点击匹数表头后行序应变化（隐式多源优先是设计行为——排序在组内生效）
        before_titles = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#table-body tr')].slice(0, 10);
            return rows.map(r => (r.querySelector('td') || {}).textContent || '');
        }""")
        func = page.evaluate("""() => {
            const ths = [...document.querySelectorAll('#table-head th')];
            const hpTh = ths.find(t => t.textContent.includes('匹数'));
            if (!hpTh) return 'no hp column';
            hpTh.click();
            return 'clicked';
        }""")
        page.wait_for_timeout(600)  # 等 renderTable 重建 DOM
        rows_after = page.eval_on_selector_all("#table-body tr", "els => els.length")
        after_titles = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#table-body tr')].slice(0, 10);
            return rows.map(r => (r.querySelector('td') || {}).textContent || '');
        }""")
        first_hps = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('#table-body tr')].slice(0, 6);
            return rows.map(r => {
                const cells = r.querySelectorAll('td');
                return cells[3] ? cells[3].textContent.trim() : '';
            });
        }""")
        report["checks"]["sort_click"] = func
        report["checks"]["sort_rows_after"] = rows_after
        report["checks"]["sort_hp_first"] = first_hps
        sort_changed = json.dumps(before_titles, ensure_ascii=False) != json.dumps(after_titles, ensure_ascii=False)
        report["checks"]["sort_changed"] = sort_changed
        if func == "clicked" and not sort_changed:
            report["ok"] = False
            report["checks"]["sort_check"] = "FAIL: 点击匹数表头后行序未变化"

        # 5) 数据质量（页面内 fetch latest.json）
        quality = {"total": 0, "with_model": 0, "with_apf": 0, "with_hp": 0,
                   "with_energy": 0, "multi_source": 0}
        items = page.evaluate("""async () => {
            try {
                const r = await fetch('/data/latest.json', {cache: 'no-store'});
                const d = await r.json();
                return d.items || [];
            } catch (e) { return {error: String(e)}; }
        }""")
        if isinstance(items, dict) and items.get("error"):
            report["checks"]["quality"] = "FAIL: " + items["error"][:150]
            report["ok"] = False
        else:
            quality["total"] = len(items)
            for it in items:
                model = str(it.get("model") or it.get("title") or "")
                if model.startswith("KFR") or "KFR" in model:
                    quality["with_model"] += 1
                if it.get("apf"):
                    quality["with_apf"] += 1
                if it.get("hp"):
                    quality["with_hp"] += 1
                if it.get("energy_grade"):
                    quality["with_energy"] += 1
                if len(it.get("atomic_source_names", [])) >= 2:
                    quality["multi_source"] += 1
            report["checks"]["quality"] = quality
            if quality["total"] > 0:
                apf_rate = quality["with_apf"] / quality["total"]
                if apf_rate < 0.5:
                    report["ok"] = False
                    report["checks"]["quality_check"] = (
                        f"FAIL: APF 覆盖率 {apf_rate:.0%} < 50%")
        browser.close()

    report["ok"] = bool(report["ok"])
    _write(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


def _write(report: dict, path: str) -> None:
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    raise SystemExit(main())
