#!/usr/bin/env python3
"""AI-assisted extraction of hardware facts missing from spec tables:
外机铜管排数 (coil_rows) and 节流装置类型 (throttle_type: 电子膨胀阀/毛细管).

Neither PConline, ZOL nor JD spec tables carry these fields (verified
2026-08-07: 0 matches for 节流/膨胀阀/毛细管/铜管/换热器 on the PConline
full-spec page).  This script asks an LLM (with web-verification instructions)
for each model that still has unknown hardware facts, requires a positive
evidence URL for every claimed value, and caches results incrementally in
``crawl_state/hardware_cache.json`` so re-runs only process unknown items.

Values without positive evidence stay "未知" (fail closed).  The free-first
router logic mirrors crawl_phones' ai_verify_root_status.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_FILE = Path(
    os.environ.get("HARDWARE_CACHE", "crawl_state/hardware_cache.json")
)
MAX_BATCH = int(os.environ.get("HARDWARE_MAX_BATCH", "6"))
MAX_TOKENS = int(os.environ.get("HARDWARE_MAX_TOKENS", "400"))
API_TIMEOUT = int(os.environ.get("HARDWARE_API_TIMEOUT", "40"))
MAX_RETRIES = 3
MIN_REQUEST_INTERVAL = 0.8
_last_request_time = 0.0

# 免费优先端点路由（与 free_first_router.py 相同策略，顺序即优先级）
ENDPOINTS = (
    ("NVIDIA_NIM_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions",
     "z-ai/glm-5.2"),
    ("NVIDIA_NIM_API_KEY", "https://integrate.api.nvidia.com/v1/chat/completions",
     "moonshotai/kimi-k2.6"),
    ("VOLCENGINE_AGENT_PLAN_API_KEY",
     "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
     "deepseek-v4-flash"),
)

KEY_HINTS = {
    "coil_rows": ("单排", "1.6排", "1.5排", "双排", "两排", "2排"),
    "throttle_type": ("电子膨胀阀", "毛细管"),
}


def _load_cache() -> dict[str, dict[str, Any]]:
    try:
        if CACHE_FILE.exists():
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def _get_key(name: str) -> str:
    return os.environ.get(name, "")


def _llm_call(prompt: str) -> str | None:
    """Free-first LLM call; returns raw assistant text or None."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    for key_name, url, model in ENDPOINTS:
        key = _get_key(key_name)
        if not key:
            continue
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                body = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": MAX_TOKENS,
                }).encode()
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"},
                )
                with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                _last_request_time = time.monotonic()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                print(f"LLM {model} attempt {attempt} failed: {type(exc).__name__}")
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
    return None


def _parse_hardware(text: str) -> dict[str, Any]:
    """Parse the LLM answer into coil_rows/throttle_type + evidence URL."""
    result: dict[str, Any] = {}
    for field, hints in KEY_HINTS.items():
        found = None
        for hint in hints:
            if hint in text:
                found = hint
                break
        if found:
            result[field] = found
    url_match = re.search(r"https?://[^\s)\]\"]+", text)
    if url_match:
        result["evidence_url"] = url_match.group(0)
    if not result:
        result["note"] = "no positive evidence in answer"
    return result


def build_prompt(model: str, brand: str, context: str) -> str:
    return (
        f"请联网查证空调型号的硬件用料。型号：{brand} {model}。\n"
        f"已知信息（可能不完整）：{context[:400]}\n"
        f"需要查证两项：\n"
        f"1. 外机换热器铜管排数（单排/1.6排/双排等）——优先参考拆机评测、"
        f"商品详情页、厂商参数表；\n"
        f"2. 节流装置类型（电子膨胀阀/毛细管）。\n"
        f"要求：每一项都必须给出具体来源 URL（评测文章/商品页/厂商页）；"
        f"查不到就明确写'未知'，不得猜测。\n"
        f"回复格式（严格按行）：\n"
        f"铜管排数: <值或未知>\n"
        f"节流装置: <值或未知>\n"
        f"来源: <URL>\n"
        f"备注: <一句话说明>"
    )


def process_items(items: list[dict[str, Any]], max_items: int = 50) -> int:
    cache = _load_cache()
    pending = [
        item for item in items
        if str(item.get("throttle_type") or "") == "未知"
        or str(item.get("coil_rows") or "") == "未知"
    ]
    # 限量处理：每轮最多 max_items 条（0=不限），其余下轮续跑
    if max_items and len(pending) > max_items:
        print(f"limiting this run to {max_items} of {len(pending)} pending items")
        pending = pending[:max_items]
    print(f"pending hardware enrichment: {len(pending)} / {len(items)}")
    updated = 0
    for batch_start in range(0, len(pending), MAX_BATCH):
        batch = pending[batch_start:batch_start + MAX_BATCH]
        for item in batch:
            identity = str(item.get("identity_key") or item.get("model") or "")
            if not identity:
                continue
            if identity in cache and cache[identity].get("final"):
                item["throttle_type"] = cache[identity].get("throttle_type", "未知")
                item["coil_rows"] = cache[identity].get("coil_rows", "未知")
                item["hardware_evidence_url"] = cache[identity].get("evidence_url", "")
                updated += 1
                continue
            prompt = build_prompt(
                str(item.get("model") or ""),
                str(item.get("brand") or ""),
                json.dumps({
                    k: item.get(k) for k in ("ac_type", "hp", "cooling_capacity",
                                             "series", "title")
                    if item.get(k)
                }, ensure_ascii=False),
            )
            answer = _llm_call(prompt)
            if not answer:
                print(f"LLM unavailable for {identity}; keep unknown")
                continue
            parsed = _parse_hardware(answer)
            parsed["final"] = bool(parsed.get("coil_rows") and parsed.get("throttle_type")
                                   and parsed.get("evidence_url"))
            parsed["answer_excerpt"] = answer[:200]
            cache[identity] = parsed
            item["throttle_type"] = parsed.get("throttle_type", "未知")
            item["coil_rows"] = parsed.get("coil_rows", "未知")
            if parsed.get("evidence_url"):
                item["hardware_evidence_url"] = parsed["evidence_url"]
            updated += 1
        _save_cache(cache)
        print(f"batch done ({batch_start + len(batch)}/{len(pending)}), "
              f"cache size {len(cache)}")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="merged candidate json")
    parser.add_argument("--output", required=True, help="enriched candidate json")
    parser.add_argument("--max-items", type=int, default=50,
                        help="max items to enrich per run (0=unlimited)")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        print("FAIL: no items")
        return 2
    updated = process_items(items, max_items=args.max_items)
    data["items"] = items
    data["pipeline"]["hardware_enriched"] = updated
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"enriched output: {out} ({len(items)} items, {updated} touched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
