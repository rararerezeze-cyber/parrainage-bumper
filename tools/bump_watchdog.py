#!/usr/bin/env python3
"""Missed-run watchdog for bump_autres.yml.

Runs hourly (.github/workflows/bump_autres_watchdog.yml). If the most
recent completed run of bump_autres.yml is older than the expected 5h
cadence plus a 1h safety margin, and nothing is currently queued/in
progress, dispatches a workflow_dispatch catch-up run.

Safe by construction, not by hope:
  - bump_autres.yml's own concurrency group (parrainage-bumper-autres,
    cancel-in-progress: false) guarantees a catch-up dispatch and a
    legitimate scheduled run can never execute concurrently -- worst case
    they queue sequentially.
  - Each site's own per-listing cooldown (see bumper.py's "Aucune annonce
    disponible" no-op path) makes a redundant/early run a safe no-op
    rather than a double bump -- verified live 2026-08-31.
  - decide_catchup() explicitly refuses to dispatch while a run is
    queued/in_progress, so this script never piles up requests.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.bump_watch import decide_catchup, dispatch_catchup, fetch_recent_runs
from lib.notify import EVENT_WORKFLOW_ERROR, LEVEL_WARNING, emit


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("::error::GITHUB_TOKEN not set", file=sys.stderr)
        return 1

    runs = fetch_recent_runs(token)
    decision = decide_catchup(runs, now=datetime.now(timezone.utc))
    print(decision)

    if decision["action"] != "dispatch":
        return 0

    elapsed = decision["elapsed_hours"]
    print(f"MISSED RUN DETECTED: {elapsed:.2f}h since last completed run -- dispatching catch-up")
    dispatch_catchup(token)
    print("catchup_dispatched=true")
    # Surfaced in the Slack/Telegram operator status via lib.notify's
    # existing outbox -> Hermes -> Telegram path (and, for Slack, via the
    # "Autofresh bump" meta-command reading GitHub Actions directly).
    # BEST_EFFORT/FAIL_OPEN: emit() never raises, so a notify outage can
    # never turn a successful catch-up dispatch into a failed watchdog run.
    emit(
        LEVEL_WARNING,
        EVENT_WORKFLOW_ERROR,
        action="missed_scheduled_run",
        block_reason="bump_autres_missed_run",
        result=f"catchup_dispatched_after_{elapsed:.1f}h",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
