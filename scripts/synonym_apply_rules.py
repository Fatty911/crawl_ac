#!/usr/bin/env python3
"""Synonym Discovery 确定性应用器：解析 opencode Agent 的 JSON 判定 →
追加同义词映射表到 merge_data.py → 更新测试断言。

用法: python scripts/synonym_apply_rules.py agent_output.txt
无 merge 判定时退出 0（无改动）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGE = ROOT / "scripts" / "merge_data.py"
TEST = ROOT / "tests" / "test_merge_data.py"
MIN_CONFIDENCE = 0.9


def extract_decisions(text: str) -> list[dict]:
    """从 opencode 输出提取 JSON decisions（兼容 ```json 包裹/多段）。"""
    text = str(text)
    candidates = []
    for m in re.finditer(r"```json\s*\r?\n(.*?)\r?\n```", text, re.S):
        candidates.append(m.group(1))
    if not candidates:
        # 找最后的 {...} 块
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            candidates.append(m.group(0))
    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        decs = data.get("decisions")
        if isinstance(decs, list):
            return decs
    return []


def apply_rules(decisions: list[dict]) -> list[tuple[str, str, str]]:
    """返回 (field, source_value, canonical) 列表（写映射表用）。"""
    rules: list[tuple[str, str, str]] = []
    for d in decisions:
        if d.get("decision") != "merge":
            continue
        if float(d.get("confidence", 0)) < MIN_CONFIDENCE:
            continue
        field = str(d.get("field", ""))
        canonical = str(d.get("canonical", "")).strip()
        if field not in ("hp", "energy_grade", "coil_rows") or not canonical:
            continue
        for v in d.get("values", []):
            v = str(v).strip()
            if v and v != canonical:
                rules.append((field, v, canonical))
    return rules


def sync_maps(rules: list[tuple[str, str, str]]) -> bool:
    """把规则合并进 merge_data.py 的 SYNONYM_MAPS 映射表。返回是否变更。"""
    if not rules:
        return False
    src = MERGE.read_text(encoding="utf-8")

    # 构造/更新 SYNONYM_MAPS 块
    by_field: dict[str, dict[str, str]] = {}
    for field, v, canonical in rules:
        by_field.setdefault(field, {})[v] = canonical

    block_lines = [
        "# AI 自发现沉淀的同义词映射（Synonym Discovery workflow 写入，勿手改）",
        "SYNONYM_MAPS: dict[str, dict[str, str]] = {",
    ]
    for field in ("hp", "energy_grade", "coil_rows"):
        mapping = by_field.get(field)
        if not mapping:
            continue
        block_lines.append(f'    "{field}": {{')
        for v, canonical in sorted(mapping.items()):
            block_lines.append(f'        "{v}": "{canonical}",')
        block_lines.append("    },")
    block_lines.append("}")
    block = "\n".join(block_lines)

    if "SYNONYM_MAPS" in src:
        # 替换旧块
        src = re.sub(
            r"# AI 自发现沉淀的同义词映射.*?^\}\n",
            block + "\n",
            src, flags=re.S | re.M)
    else:
        # 插到 normalize_hp 定义前
        anchor = "def normalize_hp"
        src = src.replace(anchor, block + "\n\n\n" + anchor, 1)

    # 各归一函数查映射表
    lookup_lines = {
        "hp": (
            '    if _clean(value) in SYNONYM_MAPS.get("hp", {}):\n'
            '        return SYNONYM_MAPS["hp"][_clean(value)]\n'),
        "energy_grade": (
            '    if _clean(value) in SYNONYM_MAPS.get("energy_grade", {}):\n'
            '        return SYNONYM_MAPS["energy_grade"][_clean(value)]\n'),
        "coil_rows": (
            '    if _clean(value) in SYNONYM_MAPS.get("coil_rows", {}):\n'
            '        return SYNONYM_MAPS["coil_rows"][_clean(value)]\n'),
    }
    changed = src != MERGE.read_text(encoding="utf-8")
    for field, probe in (("hp", 'def normalize_hp(value: Any) -> str:'),
                         ("energy_grade", 'def normalize_energy_grade(value: Any) -> str:'),
                         ("coil_rows", 'def normalize_coil_rows(value: Any) -> str:')):
        if field not in by_field:
            continue
        if lookup_lines[field].strip() in src:
            continue  # 已有
        anchor = probe
        idx = src.find(anchor)
        if idx < 0:
            continue
        # 插到函数体开头（docstring 之后第一行）
        body_start = src.find("\n", idx)
        insert_at = src.find("\n", body_start) + 1
        src = src[:insert_at] + lookup_lines[field] + src[insert_at:]
        changed = True
    if changed:
        MERGE.write_text(src, encoding="utf-8")
    return changed


def sync_tests(rules: list[tuple[str, str, str]]) -> bool:
    """为归并规则补测试断言（简单直接）。"""
    if not rules:
        return False
    tsrc = TEST.read_text(encoding="utf-8")
    func_names = {
        "hp": "normalize_hp", "energy_grade": "normalize_energy_grade",
        "coil_rows": "normalize_coil_rows",
    }
    added = 0
    for field, v, canonical in rules:
        if f'normalize_{field}("{v}")' in tsrc:
            continue
        block = (
            "\n"
            "    def test_synonym_map(self):\n"
            "        # Synonym Discovery 自发现沉淀的归并规则\n"
            f"        from merge_data import {func_names[field]}\n"
            f"        assert {func_names[field]}(\"{v}\") == \"{canonical}\"\n"
        )
        # 插到 class TestNormalizeModelIdentity 的最后一个方法前
        anchor = "    def test_b1_suffix_normalized"
        if anchor in tsrc:
            tsrc = tsrc.replace(anchor, block + anchor, 1)
        else:
            tsrc += block
        added += 1
    if added:
        TEST.write_text(tsrc, encoding="utf-8")
    return added > 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: synonym_apply_rules.py agent_output.txt")
        return 2
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    decisions = extract_decisions(text)
    if not decisions:
        print("no decisions found in agent output (all skip / no output)")
        return 0
    rules = apply_rules(decisions)
    print(f"decisions={len(decisions)} merge_rules={len(rules)}")
    for field, v, canonical in rules:
        print(f"  {field}: {v} -> {canonical}")
    if not rules:
        return 0
    sync_maps(rules)
    sync_tests(rules)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
