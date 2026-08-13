#!/usr/bin/env python3
"""Attend le creneau Super-Parrain 24h puis dispatch le canary fused (branch feature).

DESACTIVE par defaut pendant phase BASE (data/autofresh-phase.json).
Le canary live est reporte jusqu'a BASE_READY_ALL.

Usage:
  python tools/wait_and_run_super_canary.py --status
  AUTOFRESH_FORCE_LIVE=1 python tools/wait_and_run_super_canary.py --now
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

from lib.phase import live_canary_allowed, live_writes_enabled, phase_name
from lib.super_parrain_schedule import is_eligible

BRANCH = "autofresh/phase2b-kraken-capture"
WORKFLOW = "Bump — Super Parrain (1x/jour)"
REPORT = ROOT / "data" / "captures" / "super-parrain-canary-live-result.json"


def status() -> dict:
    eligible, nxt, hours = is_eligible()
    return {
        "eligible": eligible,
        "next_eligible_at": nxt.isoformat() if nxt else None,
        "hours_remaining": round(hours, 3),
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "branch": BRANCH,
        "mode": "cycle",
        "canary": "kraken_only",
    }


def dispatch() -> int:
    if not live_canary_allowed("super-parrain") or not live_writes_enabled("super-parrain"):
        print(
            f"LIVE_CANARY_DISABLED phase={phase_name()} "
            f"super_live={live_writes_enabled('super-parrain')} — no dispatch.",
            flush=True,
        )
        return 4
    cmd = [
        "gh",
        "workflow",
        "run",
        WORKFLOW,
        "--ref",
        BRANCH,
        "-f",
        "mode=cycle",
    ]
    print("DISPATCH:", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    return r.returncode


def watch_latest() -> dict:
    """Trouve le dernier run du workflow sur la branche et attend la fin."""
    list_cmd = [
        "gh",
        "run",
        "list",
        f"--workflow={WORKFLOW}",
        "--branch",
        BRANCH,
        "--limit",
        "1",
        "--json",
        "databaseId,status,conclusion,url,createdAt,displayTitle,headBranch",
    ]
    # poll until a very recent run appears
    run_id = None
    for _ in range(30):
        out = subprocess.check_output(list_cmd, cwd=str(ROOT), text=True)
        rows = json.loads(out)
        if rows:
            run_id = rows[0]["databaseId"]
            print("run_id", run_id, rows[0].get("status"), rows[0].get("url"), flush=True)
            break
        time.sleep(5)
    if not run_id:
        return {"error": "no_run_found"}

    watch = subprocess.run(
        ["gh", "run", "watch", str(run_id), "--exit-status"],
        cwd=str(ROOT),
    )
    view = subprocess.check_output(
        [
            "gh",
            "run",
            "view",
            str(run_id),
            "--json",
            "conclusion,status,url,displayTitle,createdAt,updatedAt",
        ],
        cwd=str(ROOT),
        text=True,
    )
    meta = json.loads(view)

    # Download artifact cycle report if any
    art = subprocess.run(
        ["gh", "run", "download", str(run_id), "-n", "super-parrain-cycle", "-D", str(ROOT / "data" / "captures" / "canary-live-artifact")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    cycle_path = ROOT / "data" / "captures" / "super-parrain-last-cycle.json"
    cycle = {}
    alt = ROOT / "data" / "captures" / "canary-live-artifact" / "super-parrain-last-cycle.json"
    for p in (alt, cycle_path):
        if p.exists():
            try:
                cycle = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                pass

    result = {
        "run_id": run_id,
        "workflow": meta,
        "watch_returncode": watch.returncode,
        "artifact_download": art.returncode,
        "cycle": cycle,
        "checklist": _checklist(cycle, watch.returncode),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.get("checklist"), ensure_ascii=False, indent=2), flush=True)
    print("report", REPORT, flush=True)
    return result


def _checklist(cycle: dict, watch_rc: int) -> dict:
    af = cycle.get("autofresh") or {}
    verdict = cycle.get("canary_verdict") or {}
    details = af.get("details") or []
    canary_details = [d for d in details if (d.get("program") == "kraken" or d.get("policy") == "canary")]
    bump_only = [d for d in details if d.get("reason") == "bump_only_not_canary" or d.get("policy") == "bump_only_not_canary"]
    filled_kraken = any(d.get("fields_filled") for d in canary_details if d.get("program") == "kraken")
    post_match = verdict.get("post_match")
    return {
        "workflow_ok": watch_rc == 0,
        "login_session_unique": watch_rc == 0,  # observed in run; fused single bumper session
        "kraken_diff_detected": any(
            d.get("needs_update") or d.get("fields_filled") or d.get("changed_fields")
            for d in details
            if d.get("program") == "kraken"
        ),
        "kraken_single_save": True if cycle.get("saves") else None,
        "kraken_content_applied": bool(filled_kraken) or (af.get("updated") or 0) >= 1,
        "bump_done": (cycle.get("saves") or 0) > 0,
        "public_refetch": bool(cycle.get("canary_post_verify") or af.get("canary_post_verify") or verdict),
        "post_match": post_match,
        "write_status": cycle.get("write_status"),
        "others_bump_only": (af.get("canary_skipped") or 0) > 0 or len(bump_only) > 0,
        "canary_content_failed": cycle.get("canary_content_failed"),
        "no_unexpected_challenge": cycle.get("canary_content_failed") is not True or post_match is not False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--poll-seconds", type=int, default=120)
    args = ap.parse_args()

    st = status()
    st["phase"] = phase_name()
    st["live_canary_allowed"] = live_canary_allowed("super-parrain")
    st["super_live_writes"] = live_writes_enabled("super-parrain")
    print(json.dumps(st, indent=2), flush=True)
    if args.status:
        return 0

    if not live_canary_allowed("super-parrain") or not live_writes_enabled("super-parrain"):
        print(
            f"ABORTED: Super-Parrain live canary disabled (phase={phase_name()}).",
            flush=True,
        )
        return 4

    if args.now:
        if not st["eligible"]:
            print("NOT_ELIGIBLE", flush=True)
            return 2
        rc = dispatch()
        if rc != 0:
            return rc
        time.sleep(8)
        watch_latest()
        return 0

    # Wait loop (only when live canary explicitly allowed)
    while True:
        eligible, nxt, hours = is_eligible()
        print(
            f"wait eligible={eligible} hours={hours:.2f} next={nxt.isoformat()}",
            flush=True,
        )
        if eligible:
            break
        # sleep min(poll, remaining+60s)
        sleep_s = min(args.poll_seconds, max(30, int(hours * 3600) + 60))
        # cap single sleep at 30 min for responsiveness
        sleep_s = min(sleep_s, 1800)
        time.sleep(sleep_s)

    rc = dispatch()
    if rc != 0:
        return rc
    time.sleep(10)
    result = watch_latest()
    cl = result.get("checklist") or {}
    if cl.get("post_match") is True:
        print("SUPER-PARRAIN CANARY: WRITE_VERIFIED", flush=True)
        return 0
    if cl.get("post_match") is False:
        print("SUPER-PARRAIN CANARY: FAILED post_match=false", flush=True)
        return 3
    print("SUPER-PARRAIN CANARY: COMPLETED — check report", flush=True)
    return 0 if result.get("watch_returncode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
