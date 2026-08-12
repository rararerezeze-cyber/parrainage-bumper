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
    rows = {r["platform"]: r for r in matrix.get("platform_rows") or []}
    row = rows.get(pid) or {}
    mapped = row.get("mapped_count") or 0
    full = row.get("full_quality") or 0
    partial = row.get("partial_quality") or 0
    read = row.get("read") or "?"
    write = row.get("write") or "?"

    # Overrides for BASE labels
    if pid == "super-parrain" and full >= 29:
        base = "BASE_READY"
    elif pid == "parrainage-co" and full >= 20:
        base = "BASE_READY" if partial == 0 else "BASE_PARTIAL"
    elif pid == "code-parrainage":
        # auth full_edit for almost all; one partial is acceptable for BASE_READY
        if full >= 20 and partial <= 2:
            base = "BASE_READY"
        elif full >= 15:
            base = "BASE_PARTIAL"
        else:
            base = "IN_PROGRESS"
    elif pid == "1parrainage":
        base = "BASE_READY" if full >= 7 and partial == 0 else (
            "BASE_PARTIAL" if full >= 5 else "IN_PROGRESS"
        )

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
    # Stricter gates for BASE_READY_ALL (not just inventory)
    from platforms.parrainage_co.writer import build_write_plan as pco_plan
    from platforms.super_parrain.writer import build_write_plan as sp_plan

    writers_ok = 0
    writer_notes = []
    try:
        sp = sp_plan(program="kraken")
        if sp.structure_preserved:
            writers_ok += 1
            writer_notes.append("super-parrain:dry_plan_ok")
    except Exception as exc:  # noqa: BLE001
        writer_notes.append(f"super-parrain:fail:{exc}")
    try:
        pc = pco_plan(program="kraken")
        if pc.structure_preserved:
            writers_ok += 1
            writer_notes.append("parrainage-co:dry_plan_ok")
    except Exception as exc:  # noqa: BLE001
        writer_notes.append(f"parrainage-co:fail:{exc}")

    blockers = []
    if not live_writes_enabled() is False:
        pass
    # code-parrainage still partial capture quality cell
    for p in platforms:
        if p["platform"] == "code-parrainage" and p.get("partial", 0) > 0:
            blockers.append("code-parrainage:partial_capture")
        if p["platform"] == "referralcode-tv" and p.get("full", 0) < 5:
            blockers.append("referralcode-tv:thin_capture")
        if p["platform"] == "1parrainage" and p.get("mapped", 0) < 10:
            blockers.append("1parrainage:inventory_incomplete_vs_profile")

    report = {
        "phase": phase_name(),
        "live_writes": live_writes_enabled(),
        "live_canary_allowed": False,
        "programs": offers_n,
        "mapped_pairs": len(refs),
        "platforms": platforms,
        "base_ready_platforms": readyish,
        "writers_dry_plan_ok": writers_ok,
        "writer_notes": writer_notes,
        "blockers": blockers,
        "note": (
            "BASE_READY_ALL = all platforms BASE_READY* + no inventory blockers "
            "+ writers dry-plan for Super+Parrainage.co + live_writes off"
        ),
    }
    report["base_ready_all"] = (
        readyish >= 7
        and writers_ok >= 2
        and not blockers
        and phase_name() == "BASE"
        and not live_writes_enabled()
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
