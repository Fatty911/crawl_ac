"""Tests for the Leader official site crawler (JSON-LD parsing)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_leader as leader

SAMPLE_HTML = """
<html><head><title>LeaderKFR-72LW/LX2-1-懒人 神机AI之眼3匹柜式空调介绍价格参考-Leader官网</title></head>
<body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"懒人 神机AI之眼3匹柜式空调",
 "additionalProperty":[
   {"@type":"PropertyValue","name":"产品类型","value":"柜式空调"},
   {"@type":"PropertyValue","name":"匹数","value":"3匹"},
   {"@type":"PropertyValue","name":"变频/定频","value":"变频"},
   {"@type":"PropertyValue","name":"能效等级","value":"1级"},
   {"@type":"PropertyValue","name":"制冷量","value":"7330(900-9300)","unitText":"W"},
   {"@type":"PropertyValue","name":"制热量","value":"10000(900-12400)","unitText":"W"},
   {"@type":"PropertyValue","name":"循环风量","value":"1800","unitText":"m³/h"},
   {"@type":"PropertyValue","name":"额定制冷功率","value":"2060(300-3500)","unitText":"W"}
 ]}
</script>
</body></html>
"""

LIST_SAMPLE = """
<html><body>
<a href="https://www.leader.com.cn/air-conditioners/20260303_287394.shtml">A</a>
<a href="https://www.leader.com.cn/air-conditioners/20260303_287394.shtml">A dup</a>
<a href="https://www.leader.com.cn/air-conditioners/20260302_287390.shtml">B</a>
<a href="https://www.leader.com.cn/other/123.shtml">not ac</a>
</body></html>
"""


class TestLeaderCrawler:
    def test_extract_links_dedup(self):
        links = leader.extract_links(LIST_SAMPLE)
        assert len(links) == 2
        assert all("air-conditioners" in l for l in links)

    def test_extract_property_values(self):
        specs = leader.extract_property_values(SAMPLE_HTML)
        assert specs["匹数"] == "3匹"
        assert specs["制冷量"] == "7330(900-9300) W"
        assert specs["能效等级"] == "1级"

    def test_map_specs(self):
        specs = leader.extract_property_values(SAMPLE_HTML)
        item = leader.map_specs(specs, "LeaderKFR-72LW/LX2-1-懒人 神机AI之眼3匹柜式空调")
        assert item["model"] == "KFR-72LW/LX2-1"
        assert item["ac_type"] == "立柜式"
        assert item["hp"] == "3匹"
        assert item["inverter"] is True
        assert item["cooling_capacity"] == 7330
        assert item["heating_capacity"] == 10000
        assert item["air_flow"] == 1800
        assert item["source"] == "Leader"
        assert item["brand"] == "统帅"
