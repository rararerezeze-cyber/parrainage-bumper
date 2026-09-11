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

Each dispatch also carries a deterministic slot_id (see
lib.bump_autres_schedule.slot_id) as a workflow_dispatch input --
bump_autres.yml checks its own durable ledger before doing any real site
work, so re-dispatching the same logical slot (e.g. this scheduler
crashing after a successful dispatch but before it commits its own
"dispatched" state) can never cause a second real bump cycle.
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
    mark_recovery_dispatched,
    retryable_incomplete_slots,
    save_schedule,
    slot_id as build_slot_id,
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
    recovery = retryable_incomplete_slots(schedule, now)

    print(
        f"period={schedule['period_date']} due_slots={len(due)} "
        f"safe_recoveries={len(recovery)}"
    )

    if not due and not recovery:
        return 0

    for slot in due:
        sid = build_slot_id(schedule["period_date"], slot["index"])
        print(f"dispatching slot index={slot['index']} planned_at={slot['planned_at']} slot_id={sid}")
        dispatch_workflow(token, slot_id=sid)
        dispatch_now = datetime.now(timezone.utc)
        schedule = mark_dispatched(schedule, slot["index"], now=dispatch_now)
        save_schedule(schedule)
        dispatched_slot = next(s for s in schedule["slots"] if s["index"] == slot["index"])
        if dispatched_slot.get("catchup"):
            emit(
                LEVEL_WARNING,
                EVENT_WORKFLOW_ERROR,
                action="missed_slot_catchup",
                block_reason="bump_autres_missed_slot",
                result=f"slot_{slot['index']}_dispatched_over_{CATCHUP_TOLERANCE_MINUTES}min_late",
            )

    # One delayed recovery is allowed only for sites explicitly classified
    # safe_to_retry because no site action had started. The workflow itself
    # reads the durable per-site ledger and touches only those sites.
    for slot in recovery:
        sid = build_slot_id(schedule["period_date"], slot["index"])
        print(f"recovering slot index={slot['index']} slot_id={sid}")
        dispatch_workflow(token, slot_id=sid)
        schedule = mark_recovery_dispatched(
            schedule,
            slot["index"],
            now=datetime.now(timezone.utc),
        )
        save_schedule(schedule)

    print(f"dispatched_slots={len(due)} recovery_dispatches={len(recovery)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
