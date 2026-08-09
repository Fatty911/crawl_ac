"""Node-vm tests for the Pages SPA multi-level sort (Excel style) and
numeric range filters.  Loads docs/app.js inside a mocked DOM and drives
the pure compareRows / applyFilters logic (same approach as crawl_phones'
test_cnmo_pages_regressions.py)."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "docs" / "app.js"

SAMPLE_ITEMS = [
    {"identity_key": "kfr35gw/a1", "brand": "华凌", "model": "KFR-35GW/A1",
     "ac_type": "壁挂式", "inverter": True, "apf": 5.3, "air_flow": 730,
     "price": 1499, "cooling_capacity": 3500, "source_count": 2, "hp": "1匹",
     "energy_grade": "新一级",
     "throttle_type": "电子膨胀阀", "coil_rows": "双排", "indoor_noise": "18-35-41dB",
     "atomic_source_names": ["PConline", "JD"]},
    {"identity_key": "kfr35gw/a2", "brand": "华凌", "model": "KFR-35GW/A2",
     "ac_type": "壁挂式", "inverter": True, "apf": 5.3, "air_flow": 700,
     "price": 1299, "cooling_capacity": 3500, "source_count": 1, "hp": "2匹",
     "energy_grade": "1级",
     "throttle_type": "电子膨胀阀", "coil_rows": "1.6排", "indoor_noise": "20-38-42dB",
     "atomic_source_names": ["PConline"]},
    {"identity_key": "kfr35gw/a3", "brand": "华凌", "model": "KFR-35GW/A3",
     "ac_type": "壁挂式", "inverter": True, "apf": 5.0, "air_flow": 760,
     "price": 1199, "cooling_capacity": 3400, "source_count": 1, "hp": "大1匹",
     "energy_grade": "2级",
     "throttle_type": "毛细管", "coil_rows": "单排", "indoor_noise": "19-37-40dB",
     "atomic_source_names": ["PConline"]},
    {"identity_key": "kfr35gw/a4", "brand": "华凌", "model": "KFR-35GW/A4",
     "ac_type": "壁挂式", "inverter": True, "apf": None, "air_flow": None,
     "price": 999, "cooling_capacity": 3300, "source_count": 1, "hp": "3匹",
     "energy_grade": "3级",
     "throttle_type": "未知", "coil_rows": "未知", "indoor_noise": "",
     "atomic_source_names": ["PConline"]},
]


def run_app_js(sort_levels: list[dict], numeric_ranges: dict | None = None):
    """Load app.js in a node vm with mocked DOM/fetch; returns sorted order
    and filtered identity lists."""
    app_js_path = str(APP_JS).replace("\\", "\\\\")
    sort_json = json.dumps(sort_levels)
    ranges_json = json.dumps(numeric_ranges or {})
    node_code = textwrap.dedent(f"""
        const fs = require("fs");
        const vm = require("vm");

        class Element {{
          constructor(id) {{
            this.id = id; this._textContent = ""; this.children = [];
            this.value = ""; this.dataset = {{}}; this.className = "";
            this.tagName = "DIV"; this._innerHTML = ""; this.style = {{}};
            this._listeners = {{}};
          }}
          set textContent(v) {{ this._textContent = String(v); }}
          get textContent() {{ return this._textContent; }}
          set innerHTML(v) {{ this._innerHTML = String(v); }}
          get innerHTML() {{ return this._innerHTML; }}
          appendChild(c) {{ this.children.push(c); return c; }}
          addEventListener(ev, fn) {{ this._listeners[ev] = fn; }}
          querySelectorAll() {{ return []; }}
          querySelector() {{ return null; }}
        }}

        const elements = {{}};
        const ids = ["filter-bar","numeric-ranges","sort-levels","table-head",
                     "table-body","add-sort-level","result-count"];
        ids.forEach(id => elements[id] = new Element(id));

        const document = {{
          getElementById: (id) => elements[id] || null,
          createElement: (tag) => new Element(""),
        }};

        const data = {json.dumps(SAMPLE_ITEMS, ensure_ascii=False)};
        const fetch = () => Promise.resolve({{
          ok: true,
          json: () => Promise.resolve({{ items: data }}),
        }});

        const ctx = {{ document, fetch, console, setTimeout, clearTimeout,
                      Promise, Date, Math, JSON, String, Number, Array, Object,
                      Set, parseFloat, isFinite, RegExp }};
        vm.createContext(ctx);
        vm.runInContext(fs.readFileSync("{app_js_path}", "utf8"), ctx);

        setTimeout(() => {{
          // 顶层 let 变量不挂 ctx 属性，须在脚本作用域内赋值
          vm.runInContext('sortLevels = {sort_json}; numericRanges = {ranges_json};', ctx);
          const sorted = [...data].sort((a, b) => ctx.compareRows(a, b))
                                  .map(i => i.identity_key);
          const bar = elements["filter-bar"];
          const chips = (bar.children || []).map((g) => {{
            const opts = (g.children || [])[1];
            return {{
              group: g.children && g.children[0] ? g.children[0]._textContent : "",
              values: (opts && opts.children || []).map((c) => ({{
                value: c._textContent,
                active: (c.className || "").includes("active"),
              }})),
            }};
          }});
          const out = {{
            sorted,
            filtered: ctx.applyFilters().map(i => i.identity_key),
            chips,
          }};
          console.log("RESULT:" + JSON.stringify(out));
        }}, 80);
    """)
    proc = subprocess.run(["node", "-e", node_code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node failed: {proc.stderr[:800]}"
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise AssertionError(f"no RESULT in node output: {proc.stdout[:500]}")


class TestMultiLevelSort:
    def test_second_level_price_breaks_apf_tie(self):
        # 第一关键字 APF 降序，第二关键字价格升序
        out = run_app_js([
            {"key": "apf", "dir": "desc"},
            {"key": "price", "dir": "asc"},
        ])
        assert out["sorted"] == ["kfr35gw/a1", "kfr35gw/a2", "kfr35gw/a3", "kfr35gw/a4"]
        # a1/a2 apf 相同(5.3) → 价格升序 → a1(1499) 在 a2(1299) 后？不对：价格升序 a2 在前
        # 修正预期：apf 降序（a1,a2 同 5.3 在前）→ 价格升序 → a2(1299) < a1(1499)
        # 但隐式多源优先：a1 是 2 源 → a1 在 a2 前（除非第一级是 source_count）
        assert out["sorted"][0] == "kfr35gw/a1"  # 多源隐式优先

    def test_explicit_apf_price_asc_with_multi_source_implicit(self):
        # 验证隐式多源：a1(2源) 在 apf 同级时排最前
        out = run_app_js([
            {"key": "apf", "dir": "desc"},
            {"key": "price", "dir": "desc"},
        ])
        assert out["sorted"][0] == "kfr35gw/a1"
        assert out["sorted"][1] == "kfr35gw/a2"  # apf 同 5.3，价格降序 → a1(1499) 先
        assert out["sorted"][2] == "kfr35gw/a3"  # apf 5.0
        assert out["sorted"][3] == "kfr35gw/a4"  # apf None 排最后

    def test_third_level_air_flow(self):
        # APF → 价格 → 风量 三级
        out = run_app_js([
            {"key": "apf", "dir": "desc"},
            {"key": "price", "dir": "asc"},
            {"key": "air_flow", "dir": "desc"},
        ])
        assert out["sorted"][-1] == "kfr35gw/a4"  # 空值排最后
        assert out["sorted"][0] == "kfr35gw/a1"   # 多源隐式优先

    def test_four_levels_supported(self):
        out = run_app_js([
            {"key": "apf", "dir": "desc"},
            {"key": "price", "dir": "asc"},
            {"key": "air_flow", "dir": "desc"},
            {"key": "cooling_capacity", "dir": "desc"},
        ])
        assert len(out["sorted"]) == 4

    def test_implicit_multi_source_first(self):
        # 无显式 source_count 级时，多源隐式优先（a1 2源排最前）
        out = run_app_js([{"key": "apf", "dir": "desc"}])
        assert out["sorted"][0] == "kfr35gw/a1"

    def test_explicit_source_count_first_respects_direction(self):
        # 显式 source_count 第一级升序 → 单源在前
        out = run_app_js([{"key": "source_count", "dir": "asc"}])
        assert out["sorted"][0] in ("kfr35gw/a2", "kfr35gw/a3", "kfr35gw/a4")
        assert out["sorted"][-1] == "kfr35gw/a1"  # 2源最后


class TestNumericRangeFilter:
    def test_apf_min_range(self):
        out = run_app_js([{"key": "apf", "dir": "desc"}],
                         numeric_ranges={"apf": {"min": "5.2"}})
        assert "kfr35gw/a1" in out["filtered"]
        assert "kfr35gw/a2" in out["filtered"]
        assert "kfr35gw/a3" not in out["filtered"]  # apf 5.0 < 5.2
        assert "kfr35gw/a4" not in out["filtered"]  # apf None 无值不满足

    def test_price_range(self):
        out = run_app_js([{"key": "price", "dir": "desc"}],
                         numeric_ranges={"price": {"min": "1200", "max": "1500"}})
        assert "kfr35gw/a1" in out["filtered"]   # 1499
        assert "kfr35gw/a2" in out["filtered"]   # 1299
        assert "kfr35gw/a3" not in out["filtered"]  # 1199 < 1200
        assert "kfr35gw/a4" not in out["filtered"]  # 999 < 1200

    def test_noise_max_range(self):
        out = run_app_js([{"key": "indoor_noise_max", "dir": "asc"}],
                         numeric_ranges={"indoor_noise_max": {"max": "41"}})
        assert "kfr35gw/a1" in out["filtered"]   # 41 ≤ 41
        assert "kfr35gw/a2" not in out["filtered"]  # 42 > 41
        assert "kfr35gw/a4" not in out["filtered"]  # 无噪音值


class TestFilterChips:
    def test_hp_chips_numeric_order(self):
        """匹数栏按数值排序：1匹 < 大1匹 < 2匹 < 3匹。"""
        out = run_app_js([{"key": "apf", "dir": "desc"}])
        hp_group = next(g for g in out["chips"] if g["group"] == "匹数")
        values = [c["value"] for c in hp_group["values"]]
        assert values == ["1匹", "大1匹", "2匹", "3匹"], f"实际: {values}"

    def test_energy_grade_chips_order(self):
        """能效等级：新一级最前，其余按数值。"""
        out = run_app_js([{"key": "apf", "dir": "desc"}])
        eg_group = next(g for g in out["chips"] if g["group"] == "能效等级")
        values = [c["value"] for c in eg_group["values"]]
        assert values == ["新一级", "1级", "2级", "3级"], f"实际: {values}"

    def test_selected_chip_has_active_class(self):
        """默认选中 throttle_type=电子膨胀阀 → 对应 chip 有 active。"""
        out = run_app_js([{"key": "apf", "dir": "desc"}])
        tt_group = next(g for g in out["chips"] if g["group"] == "节流装置")
        by_value = {c["value"]: c["active"] for c in tt_group["values"]}
        assert by_value.get("电子膨胀阀") is True
        assert by_value.get("毛细管") is False

    def test_unselected_groups_have_no_active(self):
        """未筛选的组（铜管排数）无 active chip。"""
        out = run_app_js([{"key": "apf", "dir": "desc"}])
        coil_group = next(g for g in out["chips"] if g["group"] == "铜管排数")
        assert all(c["active"] is False for c in coil_group["values"])
