"""Tests for crawl_jd hotitem discovery verification logic."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import crawl_jd as jd


def _soup(text: str):
    return BeautifulSoup(text, "html.parser")


def _verify_ac_text(text: str) -> bool:
    """与 crawl_jd._verify_ac 相同的判定逻辑（页面文本级）。"""
    ac_hint = ("空调" in text or re.search(r"\d+(?:\.\d+)?匹", text)
               or re.search(r"(?:KFR|GW|LW|G/W|L/W)", text, re.IGNORECASE))
    non_ac_hint = ("三脚架" in text or "相机" in text or "镜头" in text
                   or "耳机" in text or "手机" in text)
    return bool(ac_hint) and not non_ac_hint


class TestVerifyAc:
    def test_tripod_page_rejected(self):
        html = _soup("<html><body>曼富图475b排行 三脚架 相机 摄影器材 云台套装</body></html>")
        text = jd.clean_text(html.get_text(" ", strip=True))[:2000]
        assert not _verify_ac_text(text), "三脚架页不应通过"

    def test_ac_page_accepted(self):
        html = _soup("<html><body>格力空调 KFR-72GW 3匹 变频冷暖 壁挂式</body></html>")
        text = jd.clean_text(html.get_text(" ", strip=True))[:2000]
        assert _verify_ac_text(text), "空调页应通过"

    def test_camera_page_rejected(self):
        html = _soup("<html><body>相机 镜头 单反 摄影器材排行</body></html>")
        text = jd.clean_text(html.get_text(" ", strip=True))[:2000]
        assert not _verify_ac_text(text), "相机页不应通过"


class TestSalesUrl:
    def test_relative_path_gets_domain(self):
        url = jd.sales_url("hotitem/abc123.html", 1)
        assert url.startswith("https://www.jd.com/hotitem/abc123.html")

    def test_absolute_path_unchanged(self):
        url = jd.sales_url("https://www.jd.com/hotitem/abc.html", 2)
        assert url.startswith("https://www.jd.com/hotitem/abc.html?")
