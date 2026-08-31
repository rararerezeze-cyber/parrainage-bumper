#!/usr/bin/env python3
"""Frequent, off-peak poller for bump_autres.yml's randomized daily slots.

Runs every ~15 min via .github/workflows/bump_autres_scheduler.yml. Reads
or generates data/bump-autres-schedule.json (never rerolling an
already-started UTC day -- see lib.bump_autres_schedule), dispatches
exactly one workflow_dispatch per due, not-yet-dispatched slot, and
persists the updated state; the calling workflow commits/pushes it
(matching this repo's existing convention -- see bump_super_parrain.yml).

The real bump_autres.yml workflow carries no schedule trigger of its own
and never touches this state file: a manual/test workflow_dispatch of it
can never shift or redefine the planned slots.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.bump_autres_schedule import (
    CATCHUP_TOLERANCE_MINUTES,
    dispatch_workflow,
    due_undispatched_slots,
    ensure_schedule_for,
    mark_dispatched,
    save_schedule,
)
from lib.notify import EVENT_WORKFLOW_ERROR, LEVEL_WARNING, emit


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("::error::GITHUB_TOKEN not set", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    schedule = ensure_schedule_for(now)
    due = due_undispatched_slots(schedule, now)

    print(f"period={schedule['period_date']} due_slots={len(due)}")

    if not due:
        return 0

    for slot in due:
        print(f"dispatching slot index={slot['index']} planned_at={slot['planned_at']}")
        dispatch_workflow(token)
        dispatch_now = datetime.now(timezone.utc)
        schedule = mark_dispatched(schedule, slot["index"], now=dispatch_now)
        save_schedule(schedule)
        dispatched_slot = next(s for s in schedule["slots"] if s["index"] == slot["index"])
        if dispatched_slot.get("catchup"):
            # BEST_EFFORT/FAIL_OPEN: emit() never raises, so a notify
            # outage can never turn a successful catch-up dispatch into a
            # failed scheduler run.
            emit(
                LEVEL_WARNING,
                EVENT_WORKFLOW_ERROR,
                action="missed_slot_catchup",
                block_reason="bump_autres_missed_slot",
                result=f"slot_{slot['index']}_dispatched_over_{CATCHUP_TOLERANCE_MINUTES}min_late",
            )

    print(f"dispatched_slots={len(due)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
