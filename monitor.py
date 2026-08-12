#!/usr/bin/env python3
"""Autofresh public offer monitor (observation-only).

  python monitor.py --program kraken
  python monitor.py --all
  python monitor.py --changes
  python monitor.py --coverage
  python monitor.py --priority
  python monitor.py --production-report
  python monitor.py --impact kraken

Does NOT modify offers.json or platform ads. PC-off via GitHub Actions.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.monitor.engine import (
    MonitorEngine,
    has_business_change,
    impact_report,
    production_readiness_report,
    save_run_report,
)
from lib.monitor.history import read_history
from lib.monitor.models import ObservationStatus
from lib.monitor.registry import coverage_stats, load_registry, priority_table
from lib.offers import OffersRepository
from lib.paths import DATA_DIR

REPORT_PATH = DATA_DIR / "captures" / "monitor-last-report.json"


def cmd_coverage() -> int:
    offers = OffersRepository().load_all()
    reg = load_registry()
    stats = coverage_stats(reg, len(offers))
    print("=== PUBLIC OFFER MONITOR COVERAGE ===")
    print(f"Programs: {stats['programs_total']}")
    print(f"URL configured: {stats['official_source_configured']}/{stats['programs_total']}")
    print(f"Verified official: {stats.get('verified_official', 0)}")
    print(f"Program-specific parsers: {stats.get('program_specific_parser', 0)}")
    print(f"Deterministic parser: {stats['deterministic_parser']}")
    print(f"Structured/API sources: {stats['structured_or_api']}")
    print(f"HTML sources: {stats['html_sources']}")
    print(f"Manual/unmonitorable: {stats['manual_or_unmonitorable']}")
    print(f"Mappings total: {stats.get('mappings_total', 0)}")
    print("by_source_class:", json.dumps(stats.get("by_source_class") or {}, indent=2))
    print("by_offer_kind:", json.dumps(stats.get("by_offer_kind") or {}, indent=2))
    return 0


def cmd_priority() -> int:
    reg = load_registry()
    rows = priority_table(reg)
    print("program                 impact  source_class                         offer_kind           parser")
    for r in rows:
        print(
            f"{r['program']:22} {r['mapped_announcements']:6}  "
            f"{r['source_class']:35} {r['offer_kind']:20} {r['parser']}"
        )
    return 0


def cmd_program(program: str, *, impact: bool = False) -> int:
    eng = MonitorEngine(live_fetch=True)
    obs = eng.run_program(program)
    save_run_report([obs])
    print(json.dumps(obs.to_dict(), ensure_ascii=False, indent=2))
    if impact:
        print("--- IMPACT (dry) ---")
        print(json.dumps(impact_report(obs), ensure_ascii=False, indent=2))
    return 0


def cmd_all(*, changes_only: bool = False) -> int:
    eng = MonitorEngine(live_fetch=True)
    results = eng.run_all()
    path = save_run_report(results)
    prod = production_readiness_report(results, eng.registry)
    high = sum(1 for r in results if r.confidence.value == "HIGH")
    review = sum(1 for r in results if r.status == ObservationStatus.REVIEW)
    cand = sum(1 for r in results if r.status == ObservationStatus.CANDIDATE)
    no_ch = sum(1 for r in results if r.status == ObservationStatus.NO_CHANGE)
    err = sum(1 for r in results if r.status == ObservationStatus.ERROR)
    rej = sum(1 for r in results if r.status == ObservationStatus.REJECTED)
    print("=== PUBLIC OFFER MONITOR ===")
    print(f"Programs scanned: {len(results)}")
    print(f"NO_CHANGE: {no_ch}")
    print(f"CANDIDATE: {cand}")
    print(f"REVIEW: {review}")
    print(f"REJECTED: {rej}")
    print(f"ERROR: {err}")
    print(f"HIGH confidence: {high}")
    print(f"business_change/should_commit: {json.loads(path.read_text(encoding='utf-8')).get('should_commit')}")
    print(f"report: {path}")
    print("--- PRODUCTION ---")
    for k in (
        "MONITOR_VERIFIED",
        "PUBLIC_MONITORABLE_PENDING",
        "APP_PERSONALIZED",
        "OPERATOR_ONLY",
        "ANTI_BOT_BLOCKED",
        "BROKEN",
        "fetch_success",
        "stable_high",
        "mappings_impacted_by_verified",
        "MONITORING_PRODUCTION_READY",
    ):
        print(f"  {k}: {prod.get(k)}")
    shown = 0
    for r in results:
        if changes_only and r.status in {
            ObservationStatus.NO_CHANGE,
            ObservationStatus.SKIPPED,
        }:
            continue
        if r.changes or r.status in {
            ObservationStatus.CANDIDATE,
            ObservationStatus.REVIEW,
            ObservationStatus.ERROR,
            ObservationStatus.REJECTED,
            ObservationStatus.SKIPPED,
        }:
            print(
                f"  {r.program:20} {r.status.value:12} conf={r.confidence.value:6} "
                f"fail={r.failure_code.value:18} mon={r.monitor_status:28} "
                f"impact={r.impact_count} notes={r.notes[:2]}"
            )
            shown += 1
    if changes_only and shown == 0:
        print("  (no business changes detected)")
    return 0


def cmd_changes() -> int:
    hist = read_history(limit=100)
    print(f"history entries: {len(hist)}")
    for h in hist[-30:]:
        print(
            f"  {h.get('detected_at','?')[:19]} {h.get('program')} "
            f"{h.get('field')}: {h.get('old')!r} -> {h.get('new')!r} "
            f"[{h.get('status')}/{h.get('confidence')}/{h.get('failure_code','')}]"
        )
    return cmd_all(changes_only=True)


def cmd_production_report() -> int:
    if REPORT_PATH.exists():
        prev = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        if prev.get("production"):
            prod = prev["production"]
            # refresh from observations if present
            from lib.monitor.models import (
                Confidence,
                FailureCode,
                Observation,
                ObservationStatus,
            )

            obs = []
            for o in prev.get("observations") or []:
                obs.append(
                    Observation(
                        program=o["program"],
                        status=ObservationStatus(o["status"]),
                        confidence=Confidence(o["confidence"]),
                        source_url=o.get("source_url"),
                        parser=o.get("parser") or "",
                        detected_at=o.get("detected_at") or "",
                        canonical_fields=o.get("canonical_fields") or {},
                        observed_fields=o.get("observed_fields") or {},
                        failure_code=FailureCode(o.get("failure_code") or "NONE"),
                        source_class=o.get("source_class") or "UNVERIFIED",
                        offer_kind=o.get("offer_kind") or "PUBLIC_CAMPAIGN",
                        monitor_status=o.get("monitor_status") or "PUBLIC_MONITORABLE_PENDING",
                        high_streak=int(o.get("high_streak") or 0),
                        impact_count=int(o.get("impact_count") or 0),
                        business_fingerprint=o.get("business_fingerprint") or "",
                    )
                )
            if obs:
                prod = production_readiness_report(obs)
        else:
            prod = prev
    else:
        print("No report yet — run monitor.py --all first")
        return 1
    print("=== MONITOR HARDENING / PRODUCTION GATE ===")
    print(json.dumps(prod, ensure_ascii=False, indent=2))
    return 0


def cmd_should_commit() -> int:
    """Exit 0 if commit needed, 1 if no business change (for GH Actions)."""
    if not REPORT_PATH.exists():
        print("should_commit=true (no previous report)")
        return 0
    data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    if data.get("should_commit") is False:
        print("should_commit=false")
        return 1
    # recompute against self (same run always false unless we have stored signature)
    # For GH: after run, should_commit is in report vs previous — already computed at save
    sc = data.get("should_commit", True)
    print(f"should_commit={str(sc).lower()}")
    return 0 if sc else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Autofresh public offer monitor")
    p.add_argument("--program", help="Single program slug")
    p.add_argument("--all", action="store_true")
    p.add_argument("--changes", action="store_true")
    p.add_argument("--coverage", action="store_true")
    p.add_argument("--priority", action="store_true")
    p.add_argument("--production-report", action="store_true")
    p.add_argument("--should-commit", action="store_true", help="Exit 1 if no business change")
    p.add_argument("--impact", action="store_true", help="With --program: show announcement impact dry")
    args = p.parse_args()

    if args.coverage:
        return cmd_coverage()
    if args.priority:
        return cmd_priority()
    if args.production_report:
        return cmd_production_report()
    if args.should_commit:
        return cmd_should_commit()
    if args.changes:
        return cmd_changes()
    if args.program:
        return cmd_program(args.program, impact=args.impact)
    if args.all:
        return cmd_all(changes_only=False)
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
