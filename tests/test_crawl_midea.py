"""Tests for the Midea mall crawler (card parsing)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_midea as midea

CARD_HUALING = ("自营 华凌空调 KFR-35GW/N8HL1Max 变频 空调挂机 极地白 大1.5匹"
                "一级能效 限时优惠每满1000减50¥1849.00¥2099.00")
CARD_MIDEA = ("美的【风尊二代pro】大1.5匹一级能效全面风 双排纯铜管空调挂机 "
              "KFR-35GW/N8MXC1ⅡPro 限时直降 ¥2681.00 ¥3899.00")
CARD_ACCESSORY = ("自营 华凌空调遥控器新旧版本随机发通用款无需配置 ¥19.00")
CARD_COLMO = ("COLMO KFR-72LW/CA1F 3匹 新一级能效 立柜式 ¥15599.00")


class TestMideaCrawler:
    def test_parse_hualing_card(self):
        item = midea.parse_card(CARD_HUALING)
        assert item is not None
        assert item["brand"] == "华凌"
        assert item["model"] == "KFR-35GW/N8HL1MAX"
        assert item["hp"] == "大1.5匹"
        assert item["energy_grade"] == "一级能效"
        assert item["ac_type"] == "壁挂式"
        assert item["inverter"] is True
        assert item["price"] == 1849.0

    def test_parse_midea_card(self):
        item = midea.parse_card(CARD_MIDEA)
        assert item is not None
        assert item["brand"] == "美的"
        assert item["model"] == "KFR-35GW/N8MXC1ⅡPRO"
        assert item["price"] == 2681.0

    def test_accessory_filtered(self):
        # 无 KFR 型号/低价配件 → None
        assert midea.parse_card(CARD_ACCESSORY) is None

    def test_colmo_brand(self):
        item = midea.parse_card(CARD_COLMO)
        assert item is not None
        assert item["brand"] == "COLMO"
        assert item["ac_type"] == "立柜式"
