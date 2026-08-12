#!/usr/bin/env python3
"""Decide l'action Super-Parrain du cron: write | bump | wait.

Usage:
  python tools/super_parrain_decide.py
  python tools/super_parrain_decide.py --enqueue-kraken
  python tools/super_parrain_decide.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_schedule import (  # noqa: E402
    decide_super_parrain_action,
    enqueue_pending,
    is_eligible,
    list_pending_super_parrain,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--enqueue-kraken", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.enqueue_kraken:
        item = enqueue_pending("super-parrain", "kraken", "fr", reason="content_update_offers")
        print("enqueued", item["key"], "next_eligible_at", item["next_eligible_at"])

    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()
    decision["eligible_now"] = eligible
    decision["pending_count"] = len(list_pending_super_parrain())

    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        print(f"action={decision['action']}")
        print(f"reason={decision.get('reason')}")
        print(f"eligible_now={eligible}")
        print(f"next_eligible_at={nxt.isoformat()}")
        print(f"hours_remaining={hours:.2f}")
        print(f"pending_count={decision['pending_count']}")
        if decision.get("program"):
            print(f"write_program={decision['program']}")
    # Exit codes for shell branching in GHA
    # 0=bump, 10=write, 20=wait
    if decision["action"] == "write":
        return 10
    if decision["action"] == "wait":
        return 20
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
