#!/usr/bin/env python3
"""Audit the AC Pages payload: structure, publication eligibility, identity
duplicates, and source regression against the published baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_ITEM_FIELDS = (
    "identity_key",
    "title",
    "brand",
    "model",
    "ac_type",
    "inverter",
    "apf",
    "atomic_source_names",
    "source_count",
    "source_urls",
    "source_ranks",
)


def audit_payload(payload: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    items = payload.get("items")
    if not isinstance(items, list):
        return ["items missing or not a list"]

    seen_identities: set[str] = set()
    for index, item in enumerate(items):
        label = f"item[{index}]"
        for field in REQUIRED_ITEM_FIELDS:
            if field not in item:
                errors.append(f"{label}: missing field {field}")
        identity = item.get("identity_key")
        if identity:
            if identity in seen_identities:
                errors.append(f"{label}: duplicate identity {identity}")
            seen_identities.add(identity)
        ac_type = str(item.get("ac_type") or "")
        if ac_type not in ("壁挂式", "立柜式"):
            errors.append(f"{label}: ineligible ac_type {ac_type!r}")
        if item.get("inverter") is not True:
            errors.append(f"{label}: ineligible inverter {item.get('inverter')!r}")
        apf = item.get("apf")
        try:
            apf_value = float(apf) if apf not in (None, "") else None
        except (TypeError, ValueError):
            apf_value = None
        if apf_value is None:
            errors.append(f"{label}: missing apf")
        else:
            floor = 4.2 if "柜" in ac_type else 5.0
            if apf_value < floor:
                errors.append(f"{label}: apf {apf_value} < floor {floor}")
        for name in ("throttle_type", "coil_rows"):
            if name not in item:
                errors.append(f"{label}: missing {name}")

    if baseline:
        baseline_items = baseline.get("items")
        if isinstance(baseline_items, list):
            baseline_ids = {b.get("identity_key") for b in baseline_items}
            current_ids = {i.get("identity_key") for i in items}
            regressed = baseline_ids - current_ids
            if regressed:
                errors.append(
                    f"source regression: {len(regressed)} published identities "
                    f"disappeared: {sorted(regressed)[:5]}..."
                )
            # 来源整体回归：任何来源从基线消失
            baseline_sources = set()
            for b in baseline_items:
                baseline_sources.update(b.get("atomic_source_names") or [])
            current_sources = set()
            for i in items:
                current_sources.update(i.get("atomic_source_names") or [])
            missing_sources = baseline_sources - current_sources
            if missing_sources:
                errors.append(f"source regression: {sorted(missing_sources)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--baseline", default=None)
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    baseline = None
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))

    errors = audit_payload(payload, baseline)
    if errors:
        print(f"AUDIT FAIL: {len(errors)} errors")
        for error in errors[:40]:
            print(" -", error)
        return 1
    print(f"AUDIT PASS: {len(payload.get('items', []))} items, "
          f"sources={payload.get('sources')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
