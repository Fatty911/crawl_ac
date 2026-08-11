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
        assert normalize_model_identity("格力KFR-35GW/(35504)FNhAj-B1") == "kfr35gw/(35504)fnhaj"
        assert normalize_model_identity("格力KFR-35GW/(35505)FNhAj-B1") == "kfr35gw/(35505)fnhaj"
        assert normalize_model_identity("格力KFR-35GW/(35504)FNhAj-B1") != normalize_model_identity("格力KFR-35GW/(35505)FNhAj-B1")


    def test_synonym_map(self):
        # Synonym Discovery 自发现沉淀的归并规则
        from merge_data import normalize_energy_grade
        assert normalize_energy_grade("一级能效") == "新一级"
    def test_b1_suffix_normalized(self):
        # 同一机型不同源写法差异：末尾 (B1)/+B1/-B1 能效后缀归一
        assert normalize_model_identity("奥克斯KFR-35GW/BpR3AQD600(B1)") == "kfr35gw/bpr3aqd600"
        assert normalize_model_identity("TCL KFR-35GW/JD21+B1") == "kfr35gw/jd21"
        assert normalize_model_identity("美的KFR-35GW/FNhAa-B1") == "kfr35gw/fnhaa"
        # B 系不误伤：非末尾 B 保留、A 系能效版本不归一（保守）
        assert normalize_model_identity("格力KFR-72LW/NhGh3B") == "kfr72lw/nhgh3b"
        assert normalize_model_identity("格力KFR-35GW/(35504)FNhAj-A3") == "kfr35gw/(35504)fnhaj-a3"

    def test_normalize_launch_date(self):
        # 上市时间粒度可不同（年/年月），同粒度内格式统一
        from merge_data import normalize_launch_date
        assert normalize_launch_date("2024 ,3月") == "2024-03"
        assert normalize_launch_date("2024,3月") == "2024-03"
        assert normalize_launch_date("2024年3月") == "2024-03"
        assert normalize_launch_date("2026-03") == "2026-03"
        assert normalize_launch_date("2025-9") == "2025-09"
        assert normalize_launch_date("2021") == "2021"
        assert normalize_launch_date("") == ""


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




class TestPreserveInheritHardware:
    """新爬取版本不得覆盖 baseline 已提取的硬件参数。"""

    def test_inherit_throttle_from_baseline(self):
        from preserve_publish_baseline import preserve
        candidate = {"items": [
            {"identity_key": "kfr35gw/n8ha1iii-h", "brand": "华凌",
             "model": "KFR-35GW/N8HA1III-H", "ac_type": "壁挂式", "inverter": True,
             "apf": 5.3, "throttle_type": "未知", "coil_rows": "未知",
             "atomic_source_names": ["PConline"], "source_count": 1,
             "source_urls": ["u"], "source_ranks": []},
        ]}
        baseline = {"items": [
            {"identity_key": "kfr35gw/n8ha1iii-h", "brand": "华凌",
             "model": "KFR-35GW/N8HA1III-H", "ac_type": "壁挂式", "inverter": True,
             "apf": 5.3, "throttle_type": "电子膨胀阀", "coil_rows": "双排",
             "hardware_evidence_url": "https://example.com/e",
             "atomic_source_names": ["PConline"], "source_count": 1,
             "source_urls": ["u"], "source_ranks": []},
        ]}
        result = preserve(candidate, baseline)
        item = result["items"][0]
        assert item["throttle_type"] == "电子膨胀阀", "应继承 throttle_type"
        assert item["coil_rows"] == "双排", "应继承 coil_rows"
        assert item["hardware_evidence_url"] == "https://example.com/e"

    def test_no_inherit_when_candidate_has_value(self):
        from preserve_publish_baseline import preserve
        candidate = {"items": [
            {"identity_key": "kfr35gw/x", "brand": "华凌", "model": "KFR-35GW/X",
             "ac_type": "壁挂式", "inverter": True, "apf": 5.3,
             "throttle_type": "毛细管", "coil_rows": "单排",
             "atomic_source_names": ["PConline"], "source_count": 1,
             "source_urls": ["u"], "source_ranks": []},
        ]}
        baseline = {"items": [
            {"identity_key": "kfr35gw/x", "brand": "华凌", "model": "KFR-35GW/X",
             "ac_type": "壁挂式", "inverter": True, "apf": 5.3,
             "throttle_type": "电子膨胀阀", "coil_rows": "双排",
             "atomic_source_names": ["PConline"], "source_count": 1,
             "source_urls": ["u"], "source_ranks": []},
        ]}
        result = preserve(candidate, baseline)
        item = result["items"][0]
        assert item["throttle_type"] == "毛细管", "candidate 有值时不覆盖"




    def test_inherit_from_git_cache(self):
        from preserve_publish_baseline import preserve
        candidate = {"items": [
            {"identity_key": "kfr35gw/n8ha1iii-h", "brand": "华凌",
             "model": "KFR-35GW/N8HA1III-H", "ac_type": "壁挂式", "inverter": True,
             "apf": 5.3, "throttle_type": "未知", "coil_rows": "未知",
             "atomic_source_names": ["PConline"], "source_count": 1,
             "source_urls": ["u"], "source_ranks": []},
        ]}
        cache = {"kfr35gw/n8ha1iii-h": {"throttle_type": "电子膨胀阀",
                                        "coil_rows": "双排",
                                        "evidence_url": "https://example.com/e"}}
        result = preserve(candidate, None, cache)
        item = result["items"][0]
        assert item["throttle_type"] == "电子膨胀阀"
        assert item["coil_rows"] == "双排"
        assert item["hardware_evidence_url"] == "https://example.com/e"




class TestValueNormalization:
    """自发现自优化：P/匹、新一级能效/新一级、2排/双排 归并。"""

    def test_normalize_hp(self):
        from merge_data import normalize_hp
        assert normalize_hp("1.5P") == "1.5匹"
        assert normalize_hp("1.5匹") == "1.5匹"
        assert normalize_hp("3.0P") == "3匹"
        assert normalize_hp("3P") == "3匹"
        assert normalize_hp("2.0P") == "2匹"
        assert normalize_hp("大1.0P") == "大1匹"
        assert normalize_hp("大1匹") == "大1匹"
        assert normalize_hp("大1.5P") == "大1.5匹"
        assert normalize_hp("小1.5P") == "小1.5匹"
        assert normalize_hp("1.0P") == "1匹"
        assert normalize_hp("") == ""
        # 无法识别保留原值
        assert normalize_hp("变频") == "变频"

    def test_normalize_energy_grade(self):
        from merge_data import normalize_energy_grade
        assert normalize_energy_grade("新一级能效") == "新一级"
        assert normalize_energy_grade("新一级") == "新一级"
        assert normalize_energy_grade("1级") == "新一级"  # Synonym Discovery: PConline 简写=新国标新一级

    def test_normalize_coil_rows(self):
        from merge_data import normalize_coil_rows
        assert normalize_coil_rows("2排") == "双排"
        assert normalize_coil_rows("两排") == "双排"
        assert normalize_coil_rows("双排") == "双排"
        assert normalize_coil_rows("1.6排") == "1.6排"

    def test_merge_group_applies_normalization(self):
        from merge_data import merge_group
        row = {
            "identity_key": "kfr35gw/n8ha1iii-h", "model": "KFR-35GW/N8HA1III-H",
            "brand": "华凌", "source": "PConline", "atomic_source_names": ["PConline"],
            "source_product_id": "1", "source_url": "u", "source_rank": 1,
            "hp": "1.5P", "energy_grade": "新一级能效", "coil_rows": "2排",
        }
        merged = merge_group("kfr35gw/n8ha1iii-h", [row])
        assert merged["hp"] == "1.5匹"
        assert merged["energy_grade"] == "新一级"
        assert merged["coil_rows"] == "双排"




    def test_normalize_brand_leader(self):
        from merge_data import normalize_brand
        assert normalize_brand("Leader") == "统帅"
        assert normalize_brand("LEADER") == "统帅"
        assert normalize_brand("统帅") == "统帅"
        assert normalize_brand("Colmo") == "COLMO"
        assert normalize_brand("格力") == "格力"




    def test_3p_wall_mounted_apf_floor(self):
        """3匹挂机（72GW）APF 按柜机标准 4.2（大匹数挂机天花板低）。"""
        from merge_data import check_publication, tier_apf_floor
        assert tier_apf_floor("壁挂式", "3匹") == 4.2
        assert tier_apf_floor("壁挂式", "2匹") == 4.2
        assert tier_apf_floor("壁挂式", "1.5匹") == 5.0
        assert tier_apf_floor("立柜式", "3匹") == 4.2
        item = {"identity_key": "kfr72gw/x", "brand": "格力", "model": "KFR-72GW/X",
                "ac_type": "壁挂式", "inverter": True, "apf": 4.48, "hp": "3匹",
                "atomic_source_names": ["PConline"], "source_count": 1,
                "source_urls": ["u"], "source_ranks": []}
        ok, reasons = check_publication(item)
        assert ok, f"3匹挂机应通过：{reasons}"
        # 1.5匹挂机保持严格
        item2 = dict(item, hp="1.5匹", apf=4.8)
        ok2, _ = check_publication(item2)
        assert not ok2, "1.5匹挂机 APF<5.0 应拒绝"


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
