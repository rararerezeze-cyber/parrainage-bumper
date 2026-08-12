#!/usr/bin/env python3
"""Wait for Super-Parrain cooldown then run content canary (no cooldown bypass).

Uses current offers/operator effective values (real métier), not fictional amounts.
--force on controlled_write = intent confirmation only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_schedule import is_eligible


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default="kraken")
    ap.add_argument("--poll-seconds", type=int, default=300)
    ap.add_argument("--max-wait-hours", type=float, default=30.0)
    ap.add_argument("--dry-run-wait", action="store_true", help="Only report eligibility")
    args = ap.parse_args()

    start = time.time()
    while True:
        eligible, nxt, hours = is_eligible()
        now = datetime.now(timezone.utc).isoformat()
        print(f"[{now}] eligible={eligible} hours_remaining={hours:.2f} next={nxt.isoformat()}")
        if args.dry_run_wait:
            return 0 if eligible else 2
        if eligible:
            break
        if (time.time() - start) / 3600.0 > args.max_wait_hours:
            print("TIMEOUT waiting for eligibility — not forcing cooldown")
            return 3
        time.sleep(max(60, args.poll_seconds))

    # Live canary — real values only
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "controlled_write_super_parrain.py"),
        "--program",
        args.program,
        "--execute",
        "--force",  # intent confirmation; cooldown already checked
    ]
    print("Running:", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(ROOT))
    # refresh e2e status
    subprocess.call([sys.executable, str(ROOT / "tools" / "e2e_status.py")], cwd=str(ROOT))
    out = {
        "at": datetime.now(timezone.utc).isoformat(),
        "program": args.program,
        "exit_code": rc,
        "note": "Super-Parrain content canary after eligibility",
    }
    path = ROOT / "data" / "captures" / "activation-canary-waiter-result.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"result={path} exit={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
