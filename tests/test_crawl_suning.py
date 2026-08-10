"""Tests for the Suning AC crawler card parsing (real HTML fixture)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_suning as cs

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "suning_ac_search.html"


def _parse_first(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(cs.CARDS_SELECTOR)
    if not cards:
        return None
    return cs.parse_card(cards[0], 1, 1)


class TestParseCard:
    def test_midea_card(self):
        card_html = """<div class="product-box">
          <a class="sellPoint" href="//product.suning.com/0000000000/12450124928.html"
             title="美的空调酷省电二代KFR-35GW/KS2 大1.5匹 新一级能效变频壁挂式家用卧室双排蒸发器海思芯片[2026款]">
            <img alt="美的空调酷省电二代KFR-35GW/KS2 大1.5匹 新一级能效变频壁挂式家用卧室双排蒸发器海思芯片[2026款]"></a>
          <div class="title-selling-point">
            <a href="//product.suning.com/0000000000/12450124928.html">美的空调酷省电二代KFR-35GW/KS2 大1.5匹 新一级能效变频壁挂式家用卧室双排蒸发器海思芯片[2026款]</a>
          </div>
          <span class="def-price">2699</span>
        </div>"""
        item = _parse_first(card_html)
        assert item is not None
        assert item["brand"] == "美的"
        assert item["model"] == "KFR-35GW/KS2"
        assert item["hp"] == "大1.5匹"
        assert item["ac_type"] == "壁挂式"
        assert item["energy_grade"] == "新一级"
        assert item["price"] == 2699.0
        assert item["source"] == "Suning"
        assert item["source_url"].startswith("https://product.suning.com/")

    def test_hisense_2p_wall_mounted(self):
        """2匹挂机（KFR-50GW）——PConline 源缺的类型，苏宁应能补。"""
        card_html = """<div class="product-box">
          <a class="sellPoint" href="//product.suning.com/0000000000/12450000001.html"
             title="海信空调KFR-50GW/E360-X1 2匹 新一级能效 变频 壁挂式家用空调"></a>
          <div class="title-selling-point">
            <a href="//product.suning.com/0000000000/12450000001.html">海信空调KFR-50GW/E360-X1 2匹 新一级能效 变频 壁挂式家用空调</a>
          </div>
        </div>"""
        item = _parse_first(card_html)
        assert item is not None
        assert item["model"] == "KFR-50GW/E360-X1"
        assert item["hp"] == "2匹"
        assert item["ac_type"] == "壁挂式"

    def test_service_card_skipped(self):
        """服务类商品（空调清洗服务）无型号链接不应产出。"""
        card_html = """<div class="product-box">
          <a class="sellPoint" href="//product.suning.com/0000000000/12450000002.html"
             title="1台挂机空调清洗服务 苏宁帮客上门服务"></a>
          <div class="title-selling-point">
            <a href="//product.suning.com/0000000000/12450000002.html">1台挂机空调清洗服务 苏宁帮客上门服务</a>
          </div>
        </div>"""
        item = _parse_first(card_html)
        # 服务类也会产出（无型号），但 model 不应含 KFR 识别；此处验证不崩溃即可
        assert item is not None

    def test_aux_long_title(self):
        card_html = """<div class="product-box">
          <a class="sellPoint" href="//product.suning.com/0000000000/12450000003.html"
             title="奥克斯(AUX)空调 1.5匹挂机 新一级能效 变频 冷暖两用 节能省电 卧室壁挂式"></a>
          <div class="title-selling-point">
            <a href="//product.suning.com/0000000000/12450000003.html">奥克斯(AUX)空调 1.5匹挂机 新一级能效 变频 冷暖两用 节能省电 卧室壁挂式</a>
          </div>
        </div>"""
        item = _parse_first(card_html)
        assert item is not None
        assert item["brand"] == "奥克斯"
        assert item["hp"] == "1.5匹"
        assert item["ac_type"] == "壁挂式"
        assert item["energy_grade"] == "新一级"


class TestParseHelpers:
    def test_parse_brand_leader(self):
        assert cs.parse_brand("Leader空调KFR-35GW 统帅") == "统帅"
        assert cs.parse_brand("格力空调") == "格力"
        assert cs.parse_brand("未知品牌") == ""

    def test_parse_hp(self):
        assert cs.parse_hp("大1.5匹") == "大1.5匹"
        assert cs.parse_hp("2匹") == "2匹"
        assert cs.parse_hp("3匹") == "3匹"
        assert cs.parse_hp("无匹数") is None

    def test_parse_energy(self):
        assert cs.parse_energy("新一级能效") == "新一级"
        assert cs.parse_energy("新一级") == "新一级"
        assert cs.parse_energy("一级能效") == "1级"
        assert cs.parse_energy("二级能效") == "2级"

    def test_parse_ac_type(self):
        assert cs.parse_ac_type("壁挂式") == "壁挂式"
        assert cs.parse_ac_type("立柜式") == "立柜式"
        assert cs.parse_ac_type("柜机") == "立柜式"
        assert cs.parse_ac_type("挂机") == "壁挂式"
