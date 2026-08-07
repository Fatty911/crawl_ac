"""Audit tests: eligibility, duplicates, baseline regression."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_pages_payload import audit_payload  # noqa: E402


def _good_item(identity="kfr35gw/n8ha1iii-h", **overrides):
    item = {
        "identity_key": identity,
        "title": "华凌KFR-35GW/N8HA1III-H",
        "brand": "华凌",
        "model": "KFR-35GW/N8HA1III-H",
        "ac_type": "壁挂式",
        "inverter": True,
        "apf": 5.3,
        "throttle_type": "电子膨胀阀",
        "coil_rows": "双排",
        "atomic_source_names": ["PConline"],
        "source_count": 1,
        "source_urls": ["https://example.com/1"],
        "source_ranks": [{"source": "PConline", "rank": 1}],
    }
    item.update(overrides)
    return item


class TestAudit:
    def test_clean_payload(self):
        payload = {"items": [_good_item()]}
        assert audit_payload(payload, None) == []

    def test_missing_required_fields(self):
        item = _good_item()
        del item["apf"]
        payload = {"items": [item]}
        errors = audit_payload(payload, None)
        assert any("apf" in e for e in errors)

    def test_ineligible_ac_type(self):
        item = _good_item(ac_type="中央空调")
        errors = audit_payload({"items": [item]}, None)
        assert any("ac_type" in e for e in errors)

    def test_ineligible_inverter(self):
        item = _good_item(inverter=False)
        errors = audit_payload({"items": [item]}, None)
        assert any("inverter" in e for e in errors)

    def test_low_apf(self):
        item = _good_item(apf=4.8)
        errors = audit_payload({"items": [item]}, None)
        assert any("apf" in e for e in errors)

    def test_floor_standing_floor_ok(self):
        item = _good_item(ac_type="立柜式", apf=4.3)
        assert audit_payload({"items": [item]}, None) == []

    def test_duplicate_identities(self):
        payload = {"items": [_good_item(), _good_item()]}
        errors = audit_payload(payload, None)
        assert any("duplicate" in e for e in errors)

    def test_baseline_regression_detected(self):
        baseline = {"items": [_good_item("kfr72lw/nhgh3b")]}
        payload = {"items": [_good_item("kfr35gw/n8ha1iii-h")]}
        errors = audit_payload(payload, baseline)
        assert any("regression" in e for e in errors)

    def test_baseline_ok_when_superset(self):
        baseline = {"items": [_good_item("kfr35gw/n8ha1iii-h")]}
        payload = {"items": [_good_item("kfr35gw/n8ha1iii-h"),
                             _good_item("kfr72lw/nhgh3b")]}
        assert audit_payload(payload, baseline) == []

    def test_source_regression(self):
        baseline = {"items": [_good_item("a", atomic_source_names=["PConline", "JD"])]}
        payload = {"items": [_good_item("a", atomic_source_names=["PConline"])]}
        errors = audit_payload(payload, baseline)
        assert any("source regression" in e for e in errors)
