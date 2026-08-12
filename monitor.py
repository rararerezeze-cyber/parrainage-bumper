#!/usr/bin/env python3
"""Autofresh public offer monitor (observation-only).

  python monitor.py --program kraken
  python monitor.py --all
  python monitor.py --changes
  python monitor.py --coverage
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
    impact_report,
    save_run_report,
)
from lib.monitor.history import read_history
from lib.monitor.models import ObservationStatus
from lib.monitor.registry import coverage_stats, load_registry
from lib.offers import OffersRepository


def cmd_coverage() -> int:
    offers = OffersRepository().load_all()
    reg = load_registry()
    stats = coverage_stats(reg, len(offers))
    print("=== PUBLIC OFFER MONITOR COVERAGE ===")
    print(f"Programs: {stats['programs_total']}")
    print(f"Official source configured: {stats['official_source_configured']}/{stats['programs_total']}")
    print(f"Deterministic parser: {stats['deterministic_parser']}")
    print(f"Structured/API sources: {stats['structured_or_api']}")
    print(f"HTML sources: {stats['html_sources']}")
    print(f"Manual/unmonitorable: {stats['manual_or_unmonitorable']}")
    print(f"Browser required: {stats['browser_required']}")
    print(json.dumps(stats.get("by_source_type") or {}, indent=2))
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
    high = sum(1 for r in results if r.confidence.value == "HIGH")
    review = sum(1 for r in results if r.status == ObservationStatus.REVIEW)
    cand = sum(1 for r in results if r.status == ObservationStatus.CANDIDATE)
    no_ch = sum(1 for r in results if r.status == ObservationStatus.NO_CHANGE)
    print("=== PUBLIC OFFER MONITOR ===")
    print(f"Programs scanned: {len(results)}")
    print(f"NO_CHANGE: {no_ch}")
    print(f"CANDIDATE: {cand}")
    print(f"REVIEW: {review}")
    print(f"HIGH confidence: {high}")
    print(f"report: {path}")
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
        }:
            print(
                f"  {r.program:20} {r.status.value:12} conf={r.confidence.value:6} "
                f"changes={[c.field for c in r.changes]} notes={r.notes[:2]}"
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
            f"[{h.get('status')}/{h.get('confidence')}]"
        )
    return cmd_all(changes_only=True)


def main() -> int:
    p = argparse.ArgumentParser(description="Autofresh public offer monitor")
    p.add_argument("--program", help="Single program slug")
    p.add_argument("--all", action="store_true")
    p.add_argument("--changes", action="store_true")
    p.add_argument("--coverage", action="store_true")
    p.add_argument("--impact", action="store_true", help="With --program: show announcement impact dry")
    args = p.parse_args()

    if args.coverage:
        return cmd_coverage()
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
