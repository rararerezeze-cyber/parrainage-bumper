#!/usr/bin/env python3
"""Decide Super-Parrain cron action: wait | canary_exclusive | cycle.

CANARY_PENDING (not WRITE_VERIFIED): never bump; exclusive canary on next slot.
After WRITE_VERIFIED: cycle = PRE-CHECK + historical bumper.
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
    is_super_parrain_canary_pending,
    list_pending_super_parrain,
    super_parrain_runtime_mode,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--enqueue-kraken", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.enqueue_kraken:
        item = enqueue_pending(
            "super-parrain", "kraken", "fr", reason="content_update_offers"
        )
        print("enqueued", item["key"], "blocks_bump=canary_pending_gates_bump")

    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()
    decision["eligible_now"] = eligible
    decision["pending_count"] = len(list_pending_super_parrain())
    decision["runtime_mode"] = super_parrain_runtime_mode()
    decision["canary_pending"] = is_super_parrain_canary_pending()

    if args.json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    else:
        print(f"action={decision['action']}")
        print(f"reason={decision.get('reason')}")
        print(f"runtime_mode={decision.get('runtime_mode')}")
        print(f"canary_pending={decision.get('canary_pending')}")
        print(f"eligible_now={eligible}")
        print(f"next_eligible_at={nxt.isoformat()}")
        print(f"hours_remaining={hours:.2f}")
        print(f"pending_count={decision['pending_count']}")
        print(f"run_precheck={decision.get('run_precheck')}")
        print(f"run_bump={decision.get('run_bump')}")
        print(f"run_canary={decision.get('run_canary')}")
        print(f"skip_bump={decision.get('skip_bump')}")

    # 0=actionable slot (cycle or canary_exclusive), 20=wait (cooldown)
    return 20 if decision["action"] == "wait" else 0


if __name__ == "__main__":
    raise SystemExit(main())
