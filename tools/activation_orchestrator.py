#!/usr/bin/env python3
"""Activation orchestrator — sequential platform canaries after Super-Parrain.

Queue (skips referraldrop Google-blocked):
  1 super-parrain
  2 parrainage-co
  3 code-parrainage
  4 1parrainage
  5 referralcodes
  6 referralcode-tv

Commands:
  status              show ladder + next action
  next                print next platform to canary
  prepare --platform  dry-run / prepare artifacts only
  after-super         assert Super PASS then show remaining queue

Does NOT auto-bypass cooldowns. Live execute stays on controlled_write_* tools.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.write_status import (  # noqa: E402
    STATUS_AUTH_BLOCKED,
    STATUS_CANARY_READY,
    STATUS_WRITE_PREPARED,
    STATUS_WRITE_VERIFIED,
    get_platform_status,
    load_write_status,
    summary as write_summary,
)

# Ordered activation sequence
QUEUE = [
    "super-parrain",
    "parrainage-co",
    "code-parrainage",
    "1parrainage",
    "referralcodes",
    "referralcode-tv",
]
SKIP = {"referraldrop": "AUTH_BLOCKED_GOOGLE"}

PREPARE_CMDS: dict[str, list[str]] = {
    "super-parrain": [
        sys.executable,
        "tools/controlled_write_super_parrain.py",
        "--program",
        "kraken",
    ],
    "parrainage-co": [
        sys.executable,
        "tools/controlled_write_parrainage_co.py",
        "--program",
        "kraken",
    ],
    "code-parrainage": [
        sys.executable,
        "tools/controlled_write_code_parrainage.py",
        "--program",
        "kraken",
    ],
    "1parrainage": [
        sys.executable,
        "-c",
        "from platforms.oneparrainage.writer import dry_run_report; import json; print(json.dumps(dry_run_report(),indent=2,ensure_ascii=False))",
    ],
    "referralcodes": [
        sys.executable,
        "tools/prepare_referralcodes_agent_import.py",
        "--program",
        "kraken",
    ],
    "referralcode-tv": [
        sys.executable,
        "tools/probe_referralcode_tv_edit.py",
        "--public",
    ],
}


def queue_state() -> dict:
    rows = []
    for i, plat in enumerate(QUEUE, start=1):
        st = get_platform_status(plat)
        rows.append(
            {
                "order": i,
                "platform": plat,
                "status": st,
                "done": st == STATUS_WRITE_VERIFIED,
                "ready_for_canary": st in {STATUS_CANARY_READY, STATUS_WRITE_PREPARED},
            }
        )
    next_plat = None
    for r in rows:
        if not r["done"]:
            next_plat = r["platform"]
            break
    super_status = get_platform_status("super-parrain")
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "super_parrain": super_status,
        "super_pass": super_status == STATUS_WRITE_VERIFIED,
        "write_verified_count": write_summary().get("write_verified_count", 0),
        "queue": rows,
        "next": next_plat,
        "skipped": SKIP,
        "gate": {
            "after_super_only_for_auto_sequence": True,
            "note": (
                "Auto sequence of platforms 2+ should start only when "
                "super-parrain is WRITE_VERIFIED (Super PASS)."
            ),
        },
    }


def cmd_status() -> int:
    st = queue_state()
    path = ROOT / "data" / "captures" / "activation-orchestrator-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(st, ensure_ascii=False, indent=2))
    print(f"report={path}")
    print(
        f"\nNEXT={st['next']}  SUPER_PASS={st['super_pass']}  "
        f"VERIFIED={st['write_verified_count']}/7"
    )
    return 0


def cmd_next() -> int:
    st = queue_state()
    nxt = st["next"]
    if not nxt:
        print("ALL_DONE")
        return 0
    print(nxt)
    if nxt != "super-parrain" and not st["super_pass"]:
        print(
            "NOTE: Super-Parrain not WRITE_VERIFIED yet — "
            "finish Super canary before sequencing others (recommended).",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_prepare(platform: str) -> int:
    if platform not in PREPARE_CMDS:
        print(f"unknown platform {platform}", file=sys.stderr)
        return 2
    cmd = PREPARE_CMDS[platform]
    print("RUN:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def cmd_after_super() -> int:
    st = queue_state()
    if not st["super_pass"]:
        print("SUPER_FAIL_OR_PENDING")
        print(json.dumps(st["queue"][0], ensure_ascii=False, indent=2))
        print(
            "Super-Parrain is not WRITE_VERIFIED — "
            "run canary first (activation_canary / controlled_write_super_parrain)."
        )
        return 1
    print("SUPER_PASS")
    remaining = [r for r in st["queue"] if not r["done"] and r["platform"] != "super-parrain"]
    print(json.dumps({"remaining": remaining, "next": st["next"]}, ensure_ascii=False, indent=2))
    print("\nRecommended order:")
    for r in remaining:
        prep = " ".join(PREPARE_CMDS.get(r["platform"], ["(no prepare cmd)"]))
        print(f"  {r['order']}. {r['platform']} [{r['status']}] → {prep}")
    return 0


def cmd_prepare_all_pending(*, require_super_pass: bool) -> int:
    st = queue_state()
    if require_super_pass and not st["super_pass"]:
        print("Blocked: Super PASS required for prepare-all-pending", file=sys.stderr)
        return 1
    rc = 0
    for r in st["queue"]:
        if r["done"]:
            continue
        if r["platform"] == "super-parrain" and require_super_pass:
            continue
        print(f"\n=== prepare {r['platform']} ===")
        code = cmd_prepare(r["platform"])
        if code != 0:
            rc = code
            print(f"prepare failed for {r['platform']} rc={code}", file=sys.stderr)
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description="Autofresh activation orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Queue + Super PASS gate")
    sub.add_parser("next", help="Next platform to canary")
    sub.add_parser("after-super", help="After Super WRITE_VERIFIED — remaining work")

    pr = sub.add_parser("prepare", help="Dry prepare one platform")
    pr.add_argument("--platform", required=True, choices=list(PREPARE_CMDS.keys()))

    pa = sub.add_parser(
        "prepare-all-pending",
        help="Dry-prepare all not-yet WRITE_VERIFIED platforms",
    )
    pa.add_argument(
        "--require-super-pass",
        action="store_true",
        help="Refuse unless super-parrain is WRITE_VERIFIED",
    )

    args = p.parse_args()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "next":
        return cmd_next()
    if args.cmd == "after-super":
        return cmd_after_super()
    if args.cmd == "prepare":
        return cmd_prepare(args.platform)
    if args.cmd == "prepare-all-pending":
        return cmd_prepare_all_pending(require_super_pass=args.require_super_pass)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
