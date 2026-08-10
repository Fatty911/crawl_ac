"""Tests for the JD tongtianta ranking crawler (real __react_data__ fixture
captured from the user's browser on residential IP)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_jd_tower as jd

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jd_tower_rank.html"


class TestExtractFeeds:
    def test_fixture_extracts_items(self):
        html = FIXTURE.read_text(encoding="utf-8")
        items = jd.extract_feeds(html)
        assert len(items) >= 20, f"应提取 20+ 商品，实际 {len(items)}"
        # 关键商品：小米自然风 Pro（用户买过的型号）
        xiaomi = [i for i in items if "自然风" in i["title"]]
        assert xiaomi, "应有小米自然风Pro"
        assert xiaomi[0]["sku_id"] == "10150443744095"
        assert xiaomi[0]["price"] and xiaomi[0]["price"] > 1000

    def test_all_items_have_sku_and_title(self):
        html = FIXTURE.read_text(encoding="utf-8")
        items = jd.extract_feeds(html)
        for item in items:
            assert len(item["sku_id"]) >= 10
            assert len(item["title"]) >= 5

    def test_model_parsed_from_title(self):
        html = FIXTURE.read_text(encoding="utf-8")
        items = jd.extract_feeds(html)
        parsed = [jd.parse_title(dict(i)) for i in items]
        with_model = [i for i in parsed if re.match(r"KFR", i["model"])]
        assert with_model, "应解析出 KFR 型号"
        assert parsed[0]["source"] == "JD"
        assert parsed[0]["atomic_source_names"] == ["JD"]


class TestRiskPage:
    def test_risk_page_detected(self):
        assert jd.is_risk_page("<html>验证一下，购物无忧 请点击下方按钮登录</html>")
        assert jd.is_risk_page("<html>no react data here</html>")

    def test_normal_page_not_risk(self):
        html = FIXTURE.read_text(encoding="utf-8")
        assert not jd.is_risk_page(html)
