#!/usr/bin/env python3
"""Rapport BASE phase — etat vers BASE_READY_ALL."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.coverage import build_matrix, write_coverage_report
from lib.inventory import list_mapping_refs
from lib.mapping_guards import write_blocked_reason
from lib.offers import OffersRepository
from lib.phase import live_writes_enabled, phase_name
from platforms.registry import ALL_PLATFORMS, platform_capability


def platform_base_row(pid: str, matrix: dict) -> dict:
    rows = {r["platform"]: r for r in matrix.get("platforms") or []}
    row = rows.get(pid) or {}
    mapped = row.get("mapped") or 0
    full = row.get("full") or 0
    partial = row.get("partial") or 0
    read = row.get("read_state") or "?"
    write = row.get("write_state") or "?"

    # Overrides for BASE labels
    if pid == "super-parrain" and full >= 29:
        base = "BASE_READY"
    elif pid == "parrainage-co" and full >= 20:
        base = "BASE_READY" if partial == 0 else "BASE_PARTIAL"
    elif pid == "code-parrainage":
        base = "BASE_PARTIAL" if full >= 15 else "IN_PROGRESS"
        if partial:
            base = "BASE_PARTIAL"
    elif pid == "1parrainage":
        base = "BASE_PARTIAL" if full >= 5 else "IN_PROGRESS"
    elif pid == "referralcodes":
        base = "BASE_READY_MANUAL" if mapped >= 5 else "IN_PROGRESS"
    elif pid == "referralcode-tv":
        base = "BASE_READY_MANUAL" if full >= 8 else "IN_PROGRESS"
    elif pid == "referraldrop":
        base = "BASE_READY_AUTH_BLOCKED" if mapped >= 1 else "IN_PROGRESS"
    else:
        base = "IN_PROGRESS"

    # stale check
    stale = 0
    for ref in list_mapping_refs():
        if ref.platform != pid:
            continue
        if write_blocked_reason(ref.platform, ref.program, ref.language):
            # only count explicit stale/not present
            from lib.mapping_guards import load_mapping_raw

            raw = load_mapping_raw(ref.platform, ref.program, ref.language)
            st = str(raw.get("status") or "")
            if "STALE" in st or "NOT_PRESENT" in st or "NOT_ON" in st:
                stale += 1

    return {
        "platform": pid,
        "capability": platform_capability(pid),
        "mapped": mapped,
        "full": full,
        "partial": partial,
        "stale": stale,
        "read_state": read,
        "write_state": write,
        "base_status": base,
    }


def main() -> int:
    matrix = build_matrix()
    write_coverage_report()
    offers_n = len(OffersRepository().load_all())
    refs = list_mapping_refs()
    platforms = [platform_base_row(p, matrix) for p in ALL_PLATFORMS]
    readyish = sum(
        1
        for p in platforms
        if str(p["base_status"]).startswith("BASE_READY")
    )
    report = {
        "phase": phase_name(),
        "live_writes": live_writes_enabled(),
        "programs": offers_n,
        "mapped_pairs": len(refs),
        "platforms": platforms,
        "base_ready_platforms": readyish,
        "base_ready_all": readyish >= 7 and phase_name() == "BASE",
        "note": (
            "BASE_READY_ALL requires explicit review that dry-run writers are prepared "
            "and no live canary is pending. base_ready_all flag is advisory until writers "
            "matrix is complete."
        ),
    }
    # Stricter: all platforms BASE_READY*
    report["base_ready_all"] = all(
        str(p["base_status"]).startswith("BASE_READY") for p in platforms
    )
    out = ROOT / "data" / "captures" / "base-status.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("=== AUTOFRESH BASE STATUS ===")
    print(f"Phase: {report['phase']}  live_writes={report['live_writes']}")
    print(f"Programs: {offers_n}  Mapped pairs: {len(refs)}")
    for p in platforms:
        print(
            f"  {p['platform']:18} {p['base_status']:28} "
            f"mapped={p['mapped']:3} full={p['full']:3} partial={p['partial']} "
            f"stale={p['stale']} read={p['read_state']} write={p['write_state']}"
        )
    print(f"BASE_READY_ALL = {'YES' if report['base_ready_all'] else 'NO'}")
    print(f"report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
