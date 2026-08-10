#!/usr/bin/env python3
"""京东通天塔排行榜爬虫（第三源候选）。

榜单页 pro.jd.com/mall/active/xxx/index.html?rankId=N&rankType=10：
- 页面 HTML 内联 __react_data__（rankPageBase.feeds：skuId/name/price）——
  curl 拿完整 HTML 即可解析，无需渲染（住宅 IP 实测可行）
- 机场节点多数被风控（"验证一下"登录墙页，无 __react_data__）——
  风控即跳过该次请求（节点轮换随机碰，碰上没被风控的节点算运气好），
  一直失败不影响其它源（artifact 缺失时 merge 容忍）
- 榜单 URL 与 rankId 由用户从京东 App/网页发现（人工提供）

数据：skuId/名称（含型号 KFR-xxx）/价格/销量标签——供 PConline×JD 双源合并。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from scripts.crawler_utils import clean_text, get_html, make_session, parse_number
    from scripts.crawl_runtime import Budget, Progress, human_delay, read_jsonl, rewrite_jsonl
except ModuleNotFoundError:
    from crawler_utils import clean_text, get_html, make_session, parse_number
    from crawl_runtime import Budget, Progress, human_delay, read_jsonl, rewrite_jsonl

try:
    from scripts.node_rotator import make_rotator
except ModuleNotFoundError:
    from node_rotator import make_rotator

# 通天塔榜单页模板（rankId 由用户提供/发现）
TOWER_URL = ("https://pro.jd.com/mall/active/4JRfHorUDXgL77E9YdNxSCNMKwkJ/index.html"
             "?pageNum=1&queryType=1&fromName=tongtianta&bbtf=1&rankId={rank_id}"
             "&rankType=10&currSku=0")

# 用户提供的空调榜单 rankId（3匹壁挂热卖榜 687338 已实测，其余为各品类空调榜）
RANK_IDS = [687338, 3842154, 686932, 680925, 687754, 3842156, 1883785, 687312]

STATE_DIR = "crawl_state/jd"

MODEL_RE = re.compile(
    r"(KFRD?|KFD?|KF)\s*[-－]?\s*(\d{2,3})\s*([A-Z]{1,6})?\s*/?\s*([0-9A-Za-zⅢⅣ()\-]{2,30})",
    re.IGNORECASE)


def is_risk_page(html_text: str) -> bool:
    """风控/登录墙页判定：无 __react_data__ 或含验证提示。"""
    if "__react_data__" not in html_text:
        return True
    return ("验证一下" in html_text or "请点击下方按钮登录" in html_text
            or "前往登录" in html_text)


def extract_feeds(html_text: str) -> list[dict[str, Any]]:
    """从内联 __react_data__ 提取榜单商品（skuId/name/price/销量标签）。

    __react_data__ 在 HTML 中赋值为 JSON 字符串（双重转义）：
    window.__react_data__="{\\"pageData\\":...}"</script>
    """
    match = re.search(r"window\.__react_data__\s*=\s*(.+?)</script>", html_text, re.S)
    if not match:
        return []
    raw = match.group(1).strip()
    # 去掉末尾分号
    if raw.endswith(";"):
        raw = raw[:-1]
    # __react_data__ 赋值为 JSON 字符串字面量（含外引号 + 转义），
    # json.loads 直接处理（自动解包字符串 + 反转义）
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试截断修复（可能 fixture 尾部不完整）
        for end in range(len(raw), max(0, len(raw) - 200), -1):
            try:
                data = json.loads(raw[:end])
                break
            except json.JSONDecodeError:
                continue
        else:
            return []
    # 从 HTML 原始文本直接提取（__react_data__ 是双重转义 JSON 字符串——
    # JSON dump 后的字符串中 "skuId":" 变成 \"skuId\":\" 形式，
    # 直接在原始文本上用正则提取更可靠）
    raw_html = match.group(1)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    # skuId 值出现在两种形式：\"skuId\":\"NNN\" 或 "skuId":"NNN"
    for m in re.finditer(r'\\?"skuId\\?":\\?"(\d{10,15})\\?"', raw_html):
        sku = m.group(1)
        if sku in seen:
            continue
        # name 在 skuId 后不远处
        seg = raw_html[m.end():m.end() + 4000]
        name_m = re.search(r'\\?"name\\?":\\?"([^"\\]{5,150})', seg)
        price_m = re.search(r'\\?"purchasePrice\\?":\\?"([\d.]+)', seg)
        if name_m:
            seen.add(sku)
            items.append({
                "sku_id": sku,
                "title": name_m.group(1),
                "price": float(price_m.group(1)) if price_m else None,
                "sold_label": "",
            })
    return items


def parse_title(item: dict[str, Any]) -> dict[str, Any]:
    """从标题解析型号/品牌/匹数/类型（与苏宁爬虫同规则）。"""
    title = item.get("title", "")
    model_m = MODEL_RE.search(title)
    item["model"] = model_m.group(0).upper().replace(" ", "") if model_m else title[:40]
    item["source"] = "JD"
    item["atomic_source_names"] = ["JD"]
    item["source_category"] = "JD tongtianta ranking"
    item["currency"] = "CNY"
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="JD tongtianta ranking crawler")
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-budget", type=int, default=0)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--min-records", type=int, default=10)
    parser.add_argument("--progress-dir", default=STATE_DIR)
    parser.add_argument("--rank-ids", default=None,
                        help="comma-separated rankId list (default: built-in)")
    args = parser.parse_args()

    session = make_session()
    rotator = make_rotator()
    print(rotator.summary())

    rank_ids = [int(x) for x in (args.rank_ids or "").split(",") if x.strip()] or RANK_IDS
    budget = Budget(args.time_budget)
    delay = human_delay(args.delay)
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocked = 0
    ok_boards = 0

    for rank_id in rank_ids:
        if budget.expired():
            print("time budget expired, stop")
            break
        url = TOWER_URL.format(rank_id=rank_id)
        try:
            if rotator and rotator.enabled:
                node = rotator.rotate()
            html, final_url = get_html(session, url, encoding="utf-8", delay=delay)
            if rotator and rotator.enabled and node:
                rotator.mark_success(node)
        except Exception as exc:
            if rotator and rotator.enabled and node:
                rotator.mark_failure(node, blocked=True)
            print(f"rank {rank_id} failed: {type(exc).__name__}")
            blocked += 1
            continue
        text = str(html)
        if is_risk_page(text):
            print(f"rank {rank_id}: risk page (blocked), skip")
            blocked += 1
            continue
        feeds = extract_feeds(text)
        new_items = []
        for feed in feeds:
            key = feed["sku_id"]
            if key in seen:
                continue
            seen.add(key)
            new_items.append(parse_title(feed))
        if new_items:
            ok_boards += 1
            print(f"rank {rank_id}: +{len(new_items)} items (total {len(all_items) + len(new_items)})")
            all_items.extend(new_items)
        else:
            print(f"rank {rank_id}: 0 new items")

    payload = {
        "schema_version": "1.0",
        "source": "JD",
        "scraped_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
        "items": all_items,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"artifact written: {out} ({len(all_items)} items, boards ok={ok_boards}, blocked={blocked})")
    if len(all_items) < args.min_records:
        print(f"records {len(all_items)} < min {args.min_records} (may be all blocked)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
