"""Tests for the Gree official site crawler (spec text parsing + binding)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_gree as gree

SPEC_SAMPLE = """产品型号：KFR-50LW/(50502)FNhCc-B1(WIFI)
产品颜色：珊瑚玉
变频/定频：变频
APF(GB21455-2019)：4.76
能效等级：1级
冷暖类型：冷暖
匹数：2匹
适用面积(m²)：23-34
额定制冷量(W)：5110（890-7150）
额定制热量(W)：7210（700-8830）
额定制冷功率(W)：1270（190-2300）
额定制热功率(W)：1900（190-2575）
内机噪音dB(A)(静音档-高档)：27-40
循环风量(m³/h)：1050
"""


class TestGreeCrawler:
    def test_parse_specs(self):
        specs = gree.parse_specs(SPEC_SAMPLE)
        assert specs["产品型号"] == "KFR-50LW/(50502)FNhCc-B1(WIFI)"
        assert specs["APF(GB21455-2019)"] == "4.76"
        assert specs["匹数"] == "2匹"
        assert specs["能效等级"] == "1级"

    def test_map_item(self):
        specs = gree.parse_specs(SPEC_SAMPLE)
        item = gree.map_item(specs, "KFR-50LW/(50502)FNhCc-B1(WIFI)")
        assert item["model"] == "KFR-50LW/(50502)FNhCc-B1(WIFI)"
        assert item["apf"] == "4.76"
        assert item["hp"] == "2匹"
        assert item["energy_grade"] == "1级"
        assert item["inverter"] is True
        assert item["cooling_capacity"] == 5110
        assert item["heating_capacity"] == 7210
        assert item["air_flow"] == 1050
        assert item["indoor_noise"] == "27-40"
        assert item["source"] == "Gree"
        assert item["brand"] == "格力"

    def test_parse_specs_skip_no_value(self):
        specs = gree.parse_specs("产品型号：\n匹数：3匹\n")
        assert specs == {"匹数": "3匹"}
