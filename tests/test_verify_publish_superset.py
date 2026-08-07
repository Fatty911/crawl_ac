from scripts.verify_publish_superset import identities


def ac(identity_key, model, apf=5.3):
    return {
        "identity_key": identity_key,
        "title": f"华凌{model}",
        "model": f"华凌{model}",
        "brand": "华凌",
        "ac_type": "壁挂式",
        "inverter": True,
        "apf": apf,
        "throttle_type": "电子膨胀阀",
        "coil_rows": "双排",
        "source": "PConline",
        "atomic_source_names": ["PConline"],
    }


def test_superset_identity_uses_current_schema_instead_of_persisted_hash():
    baseline = {"items": [ac("legacy-hash", "KFR-35GW/N8HA1III-H")]}
    candidate = {"items": [ac("current-hash", "KFR-35GW/N8HA1III-H")]}

    assert identities(baseline, eligible_only=True) == identities(candidate)


def test_superset_identity_still_detects_a_genuinely_missing_model():
    baseline = {"items": [ac("legacy-hash", "KFR-35GW/N8HA1III-H")]}
    candidate = {"items": [ac("current-hash", "KFR-72LW/NhGh3B")]}

    assert identities(baseline, eligible_only=True) - identities(candidate)


def test_ineligible_baseline_items_not_required_in_candidate():
    baseline = {"items": [ac("a", "KFR-35GW/N8HA1III-H", apf=4.0)]}
    candidate = {"items": []}

    assert identities(baseline, eligible_only=True) == set()
