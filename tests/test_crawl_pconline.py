"""PConline AC parser tests against real captured HTML fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from crawl_pconline import (  # noqa: E402
    normalize_specs,
    parse_ac_type,
    parse_capacity_watts,
    parse_detail_specs,
    parse_inverter,
    parse_launch_date,
    parse_list_page,
    parse_list_specs,
    parse_weight_kg,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _soup(name: str) -> BeautifulSoup:
    raw = (FIXTURES / name).read_bytes()
    return BeautifulSoup(raw.decode("gb18030", errors="replace"), "html.parser")


class TestParseListPage:
    def test_real_wahin_list(self):
        html = _soup("pconline_wahin_list.html")
        items = parse_list_page(html, 1, "wahin", "华凌", None, None, 0)
        assert len(items) >= 20, f"expected >=20 cards, got {len(items)}"
        first = items[0]
        assert first["source"] == "PConline"
        assert first["source_rank"] == 1
        assert first["brand"] == "华凌"
        assert first["source_product_id"]
        assert first["source_url"].startswith("https://product.pconline.com.cn/air_condition/wahin/")
        # 列表页自带核心参数
        assert "ac_type" in first or "cooling_capacity" in first

    def test_rank_increments_per_card(self):
        html = _soup("pconline_wahin_list.html")
        items = parse_list_page(html, 1, "wahin", "华凌", None, None, 0)
        ranks = [i["source_rank"] for i in items]
        assert ranks == list(range(1, len(items) + 1))


class TestParseListSpecs:
    def test_spec_lines(self):
        html = _soup("pconline_wahin_list.html")
        card = html.select_one("#JlistItems li.item")
        assert card is not None
        specs = parse_list_specs(card)
        assert specs.get("空调类型") == "挂式空调"
        assert "适用面积" in specs
        assert "制冷量" in specs


class TestParseDetailSpecs:
    def test_real_detail(self):
        html = _soup("pconline_wahin_detail.html")
        specs = parse_detail_specs(html)
        assert specs.get("系列名称") == "超省电pro"
        # clean_text() 做 NFKC 归一：Ⅲ -> III
        assert specs.get("型号(别称)") == "KFR-35GW/N8HA1III-H"
        assert specs.get("制冷量") == "3510(150-5250)W"
        assert specs.get("全年能源消耗率(APF)") == "5.30"
        assert specs.get("制冷剂") == "R32"
        assert specs.get("循环风量") == "730m3/h"
        assert specs.get("室内机噪音") == "18-35-41dB"

    def test_normalize_mapping(self):
        html = _soup("pconline_wahin_detail.html")
        specs = parse_detail_specs(html)
        mapped = normalize_specs(specs)
        assert mapped["model"] == "KFR-35GW/N8HA1III-H"
        assert mapped["ac_type"] == "壁挂式"  # 挂式空调 -> 壁挂式
        assert mapped["inverter"] is True
        assert mapped["apf"] == 5.3
        assert mapped["cooling_capacity"] == 3510.0
        assert mapped["air_flow"] == 730.0
        assert mapped["refrigerant"] == "R32"
        assert mapped["indoor_weight"] == 9.5
        assert mapped["outdoor_weight"] == 24.0
        assert mapped["launch_date"] == "2025-03"
        assert mapped["energy_grade"] == "2级"




class TestMaxPagesZeroSemantics:
    """max_pages=0 表示不限制页数（修复：0 值语义 bug 导致 0 条记录）。"""

    def test_crawl_brand_with_zero_max_pages_fetches_pages(self, monkeypatch):
        from crawl_pconline import crawl_brand
        from crawl_runtime import Budget, Progress

        fetched = []

        def fake_get_html(session, url, encoding=None, delay=0.0):
            fetched.append(url)
            # 第一页返回真实 fixture（含产品卡）；后续页返回空列表页
            if len(fetched) == 1:
                raw = (FIXTURES / "pconline_wahin_list.html").read_bytes()
                return BeautifulSoup(raw.decode("gb18030", errors="replace"), "html.parser"), url
            empty = BeautifulSoup(
                "<html><body><ul id='JlistItems'></ul><div class='page-count'></div></body></html>",
                "html.parser",
            )
            return empty, url

        monkeypatch.setattr("crawl_pconline.get_html", fake_get_html)
        progress = Progress(current_page=1)
        items = crawl_brand(None, None, "wahin", "华凌", progress, Budget(60), 0, 0)
        assert len(items) >= 20, f"max_pages=0 应不限页数，实际 {len(items)} 条"
        assert len(fetched) >= 2, "应翻页抓取"

    def test_max_pages_caps_pages(self, monkeypatch):
        from crawl_pconline import crawl_brand
        from crawl_runtime import Budget, Progress

        fetched = []

        def fake_get_html(session, url, encoding=None, delay=0.0):
            fetched.append(url)
            raw = (FIXTURES / "pconline_wahin_list.html").read_bytes()
            return BeautifulSoup(raw.decode("gb18030", errors="replace"), "html.parser"), url

        monkeypatch.setattr("crawl_pconline.get_html", fake_get_html)
        progress = Progress(current_page=1)
        items = crawl_brand(None, None, "wahin", "华凌", progress, Budget(60), 0, 1)
        assert len(fetched) == 1, f"max_pages=1 应只抓 1 页，实际 {len(fetched)}"
        assert len(items) >= 20


class TestHelpers:
    def test_parse_ac_type(self):
        assert parse_ac_type("挂式空调") == "壁挂式"
        assert parse_ac_type("立式空调") == "立柜式"
        assert parse_ac_type("中央空调") == "中央空调"
        assert parse_ac_type("移动空调") == "移动空调"

    def test_parse_inverter(self):
        assert parse_inverter("变频") is True
        assert parse_inverter("定频") is False
        assert parse_inverter("") is None

    def test_parse_capacity(self):
        assert parse_capacity_watts("3510(150-5250)W") == 3510.0
        assert parse_capacity_watts("730m3/h") == 730.0
        assert parse_capacity_watts("") is None

    def test_parse_launch_date(self):
        assert parse_launch_date("2025年,3月") == "2025-03"
        assert parse_launch_date("2025年") == "2025"
        assert parse_launch_date("2024年12月") == "2024-12"

    def test_parse_weight(self):
        assert parse_weight_kg("9.5kg") == 9.5
        assert parse_weight_kg("24KG") == 24.0
        assert parse_weight_kg("") is None
