#!/usr/bin/env python3
"""Preserve all eligible identities from the previously published payload."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.merge_data import check_publication
except ModuleNotFoundError:
    from merge_data import check_publication


def preserve(candidate: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    candidate_items = candidate.get("items", [])
    baseline_items = (baseline or {}).get("items", [])
    eligible_baseline = [item for item in baseline_items if check_publication(item)[0]]
    candidate_ids = {item.get("identity_key") for item in candidate_items}
    preserved = [
        item for item in eligible_baseline
        if item.get("identity_key") not in candidate_ids
    ]
    merged = [*candidate_items, *preserved]
    payload = {
        "schema_version": candidate.get("schema_version", "1.0"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "count": len(merged),
        "sources": candidate.get("sources", []),
        "pipeline": {
            **(candidate.get("pipeline") or {}),
            "candidate_count": len(candidate_items),
            "baseline_count": len(eligible_baseline),
            "preserved_count": len(preserved),
        },
        "items": merged,
    }
    return payload


def read_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = preserve(
        read_payload(Path(args.candidate)) or {"items": []},
        read_payload(Path(args.baseline)),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"baseline={result['pipeline']['baseline_count']} "
        f"candidate={result['pipeline']['candidate_count']} "
        f"preserved={result['pipeline']['preserved_count']} "
        f"published={result['count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
