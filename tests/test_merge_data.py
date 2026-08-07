"""AC merge/gate unit tests: model identity, APF tiers, publication gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from merge_data import (  # noqa: E402
    check_publication,
    merge_group,
    normalize_model_identity,
    parse_apf,
    parse_hp,
    tier_apf_floor,
)


class TestNormalizeModelIdentity:
    def test_basic(self):
        assert normalize_model_identity("华凌KFR-35GW/N8HA1Ⅲ-H") == "kfr35gw/n8ha1iii-h"

    def test_jd_title_noise(self):
        assert normalize_model_identity("美的空调 1.5匹 变频 新一级 KFR-35GW/N8HA1Ⅲ-P") == "kfr35gw/n8ha1iii-p"

    def test_floor_standing(self):
        assert normalize_model_identity("格力KFR-72LW/NhGh3B") == "kfr72lw/nhgh3b"

    def test_single_cool_kf(self):
        assert normalize_model_identity("KF-26GW/26379") == "kf26gw/26379"

    def test_unknown_returns_empty(self):
        assert normalize_model_identity("空调配件铜管5米") == ""



    def test_paren_variant_kept(self):
        # 格力带括号内部代号：不同机型不得错误合并
        assert normalize_model_identity("格力KFR-35GW/(35504)FNhAj-B1") == "kfr35gw/(35504)fnhaj-b1"
        assert normalize_model_identity("格力KFR-35GW/(35505)FNhAj-B1") == "kfr35gw/(35505)fnhaj-b1"
        assert normalize_model_identity("格力KFR-35GW/(35504)FNhAj-B1") != normalize_model_identity("格力KFR-35GW/(35505)FNhAj-B1")


class TestParseHelpers:
    def test_parse_hp(self):
        assert parse_hp("1.5匹") == 1.5
        assert parse_hp("大1.5匹") == 1.5
        assert parse_hp("3匹") == 3.0
        assert parse_hp("两匹") == 2.0
        assert parse_hp("") is None

    def test_parse_apf(self):
        assert parse_apf("5.30") == 5.3
        assert parse_apf(5.3) == 5.3
        assert parse_apf(None) is None
        assert parse_apf("") is None

    def test_tier_floors(self):
        assert tier_apf_floor("壁挂式") == 5.0
        assert tier_apf_floor("立柜式") == 4.2


class TestPublicationGate:
    def test_wall_inverter_good_apf(self):
        item = {"ac_type": "壁挂式", "inverter": True, "apf": 5.3,
                "throttle_type": "电子膨胀阀", "coil_rows": "双排"}
        ok, reasons = check_publication(item)
        assert ok, reasons

    def test_floor_standing_tier(self):
        item = {"ac_type": "立柜式", "inverter": True, "apf": 4.3}
        ok, reasons = check_publication(item)
        assert ok, reasons

    def test_unknown_apf_fails_closed(self):
        item = {"ac_type": "壁挂式", "inverter": True, "apf": None}
        ok, reasons = check_publication(item)
        assert not ok
        assert any("apf" in r for r in reasons)

    def test_central_ac_rejected(self):
        item = {"ac_type": "中央空调", "inverter": True, "apf": 6.0}
        ok, reasons = check_publication(item)
        assert not ok
        assert any("ac_type" in r for r in reasons)

    def test_fixed_speed_rejected(self):
        item = {"ac_type": "壁挂式", "inverter": False, "apf": 5.3}
        ok, reasons = check_publication(item)
        assert not ok
        assert any("inverter" in r for r in reasons)

    def test_low_apf_rejected(self):
        item = {"ac_type": "壁挂式", "inverter": True, "apf": 4.8}
        ok, reasons = check_publication(item)
        assert not ok
        assert any("apf" in r for r in reasons)

    def test_defaults_hardware_fields(self):
        item = {"ac_type": "壁挂式", "inverter": True, "apf": 5.3}
        ok, _ = check_publication(item)
        assert ok
        assert item["throttle_type"] == "未知"
        assert item["coil_rows"] == "未知"


class TestMergeGroup:
    def _row(self, source, pid, **fields):
        row = {
            "identity_key": "kfr35gw/n8ha1iii-h",
            "model": "KFR-35GW/N8HA1III-H",
            "brand": "华凌",
            "source": source,
            "atomic_source_names": [source],
            "source_product_id": pid,
            "source_url": f"https://example.com/{pid}",
            "source_rank": 3,
        }
        row.update(fields)
        return row

    def test_multi_source_union(self):
        pcl = self._row("PConline", "2673699", apf=5.3, air_flow=730,
                        detail_url="d1")
        zol = self._row("ZOL", "999", apf=5.3, coil_rows="双排")
        jd = self._row("JD", "100148837203", price=1499)
        merged = merge_group("kfr35gw/n8ha1iii-h", [pcl, zol, jd])
        assert merged["source_count"] == 3
        assert merged["atomic_source_names"] == ["PConline", "ZOL", "JD"]
        assert merged["apf"] == 5.3
        assert merged["air_flow"] == 730
        assert merged["coil_rows"] == "双排"
        assert merged["price"] == 1499
        assert len(merged["source_urls"]) == 3

    def test_detail_preferred_over_list(self):
        list_row = self._row("PConline", "1", cooling_capacity=3500)
        detail_row = self._row("PConline", "1", cooling_capacity=3510,
                               detail_url="d")
        merged = merge_group("kfr35gw/n8ha1iii-h", [list_row, detail_row])
        assert merged["cooling_capacity"] == 3510

    def test_brand_mode(self):
        rows = [
            self._row("PConline", "1", brand="华凌"),
            self._row("ZOL", "2", brand="华凌"),
            self._row("JD", "3", brand="美的"),
        ]
        merged = merge_group("x", rows)
        assert merged["brand"] == "华凌"
