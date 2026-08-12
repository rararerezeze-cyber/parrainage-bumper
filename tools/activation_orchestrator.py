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

from lib.canary_gate import (  # noqa: E402
    POST_SUPER_EXECUTABLE,
    may_execute_canary,
    next_executable,
    predecessor,
    write_all_packs,
)
from lib.write_status import (  # noqa: E402
    STATUS_CANARY_READY,
    STATUS_WRITE_PREPARED,
    STATUS_WRITE_VERIFIED,
    get_platform_status,
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
        "tools/controlled_write_1parrainage.py",
        "--program",
        "kraken",
    ],
    "referralcodes": [
        sys.executable,
        "tools/controlled_write_referralcodes.py",
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
        pred = predecessor(plat)
        gate = may_execute_canary(plat, for_super=False)
        rows.append(
            {
                "order": i,
                "platform": plat,
                "status": st,
                "predecessor": pred,
                "done": st == STATUS_WRITE_VERIFIED,
                "ready_for_canary": st == STATUS_CANARY_READY,
                "may_execute_now": bool(gate.get("ok")),
                "gate_error": None if gate.get("ok") else gate.get("error"),
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
            "one_at_a_time": True,
            "predecessor_must_pass": True,
            "note": (
                "Auto sequence of platforms 2+ starts only when super-parrain "
                "is WRITE_VERIFIED. Each later platform waits for the previous "
                "WRITE_VERIFIED. Never two sessions."
            ),
        },
        "next_executable": next_executable(for_super=False).get("next"),
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


def cmd_multiprogram_dry_run() -> int:
    from tools.multiprogram_dry_run import run as mp_run

    out = mp_run(all_programs=False)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("MULTIPROGRAM_DRY_RUN_READY") == "YES" else 1


def cmd_gate(platform: str) -> int:
    gate = may_execute_canary(platform, for_super=False)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    return 0 if gate.get("ok") else 2


def cmd_next_executable() -> int:
    nxt = next_executable(for_super=False)
    print(json.dumps(nxt, ensure_ascii=False, indent=2))
    if nxt.get("next"):
        print(f"NEXT_EXECUTABLE={nxt['next']}")
        return 0
    print("NONE")
    return 1


def cmd_canary(platform: str) -> int:
    """Live-execute exactly one platform if predecessor PASSed. Never parallel."""
    import os

    gate = may_execute_canary(platform, for_super=False)
    if not gate.get("ok"):
        print(f"REFUSED: {gate.get('error')}", file=sys.stderr)
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 2
    nxt = next_executable(for_super=False).get("next")
    if nxt != platform:
        print(
            f"REFUSED: not the next executable (next={nxt}, asked={platform})",
            file=sys.stderr,
        )
        return 2
    if os.environ.get("AUTOFRESH_SEQUENCE_LIVE") != "1":
        print("REFUSED: canary execute requires AUTOFRESH_SEQUENCE_LIVE=1")
        return 2
    cmd = PREPARE_CMDS[platform] + ["--execute", "--force"]
    print("RUN:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def cmd_arm_packs() -> int:
    payload = write_all_packs("kraken")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"POST_SUPER_CANARIES_ARMED={payload.get('POST_SUPER_CANARIES_ARMED')}")
    return 0 if payload.get("POST_SUPER_CANARIES_ARMED") == "YES" else 1


def cmd_sequence_after_super(*, execute: bool) -> int:
    """After Super PASS: fire exactly ONE next platform (never the whole queue)."""
    st = queue_state()
    path = ROOT / "data" / "captures" / "activation-sequence-after-super.json"
    nxt = next_executable(for_super=False)
    if not st["super_pass"]:
        payload = {
            "result": "SUPER_PENDING",
            "super_parrain": st["super_parrain"],
            "next": st["next"],
            "next_executable": nxt.get("next"),
            "one_at_a_time": True,
            "live": False,
            "armed": list(POST_SUPER_EXECUTABLE),
            "note": (
                "Sequence armed. After Super WRITE_VERIFIED fire only "
                "parrainage-co, then wait for its PASS, then the next."
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("sequence armed — waiting Super PASS")
        return 0

    if not execute:
        remaining = [r for r in st["queue"] if not r["done"] and r["platform"] != "super-parrain"]
        payload = {
            "result": "SUPER_PASS_READY",
            "next_executable": nxt.get("next"),
            "remaining": remaining,
            "one_at_a_time": True,
            "live": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    target = nxt.get("next")
    if not target:
        print("NO_NEXT_EXECUTABLE")
        return 0
    print(f"ONE_AT_A_TIME next={target}")
    return cmd_canary(target)


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

    sub.add_parser(
        "multiprogram-dry-run",
        help="Dry-run all non-blocked platforms (kraken). No live write.",
    )
    seq = sub.add_parser(
        "sequence-after-super",
        help="After Super PASS: dry-prepare remaining queue (never live unless --execute)",
    )
    seq.add_argument(
        "--execute",
        action="store_true",
        help="Fire ONLY the next executable platform (needs AUTOFRESH_SEQUENCE_LIVE=1).",
    )

    gt = sub.add_parser("gate", help="May this platform execute a live canary now?")
    gt.add_argument("--platform", required=True)

    sub.add_parser("next-executable", help="Single next platform allowed to fire")
    sub.add_parser("arm-packs", help="Write post-Super canary packs (no live write)")

    cy = sub.add_parser("canary", help="Execute exactly one platform if it is next")
    cy.add_argument("--platform", required=True)

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
    if args.cmd == "multiprogram-dry-run":
        return cmd_multiprogram_dry_run()
    if args.cmd == "sequence-after-super":
        return cmd_sequence_after_super(execute=args.execute)
    if args.cmd == "gate":
        return cmd_gate(args.platform)
    if args.cmd == "next-executable":
        return cmd_next_executable()
    if args.cmd == "arm-packs":
        return cmd_arm_packs()
    if args.cmd == "canary":
        return cmd_canary(args.platform)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
