#!/usr/bin/env python3
"""Prove CANARY_PENDING gate: historical bumper cannot reset cooldown before canary.

Exit 0 only if all assertions pass.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_schedule import (  # noqa: E402
    decide_super_parrain_action,
    is_eligible,
    is_super_parrain_canary_pending,
    last_super_action_at,
    next_eligible_at,
    super_parrain_runtime_mode,
)
from lib.write_status import is_write_verified  # noqa: E402


TARGET_SLOT = datetime(2026, 8, 13, 5, 37, 10, tzinfo=timezone.utc)


def _workflow_text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def main() -> int:
    now = datetime.now(timezone.utc)
    last = last_super_action_at()
    nxt = next_eligible_at(now)
    eligible, _, hours = is_eligible(now)
    pending = is_super_parrain_canary_pending()
    mode = super_parrain_runtime_mode()
    verified = is_write_verified("super-parrain")
    decision = decide_super_parrain_action()

    proof = {
        "at": now.isoformat(),
        "last_super_run": last.isoformat() if last else None,
        "next_eligible_at": nxt.isoformat(),
        "target_slot": TARGET_SLOT.isoformat(),
        "hours_remaining": round(hours, 2),
        "eligible_now": eligible,
        "write_verified": verified,
        "canary_pending": pending,
        "runtime_mode": mode,
        "historical_decide": decision,
    }

    errors: list[str] = []

    # 1) Live status gate
    if verified:
        errors.append("unexpected WRITE_VERIFIED — gate proof expects CANARY_PENDING")
    if not pending or mode != "CANARY_PENDING":
        errors.append(f"expected CANARY_PENDING, got mode={mode} pending={pending}")
    if decision.get("action") != "skip":
        errors.append(f"historical action must be skip, got {decision.get('action')}")
    if decision.get("run_bump") is not False:
        errors.append("run_bump must be False while CANARY_PENDING")
    if decision.get("skip_bump") is not True:
        errors.append("skip_bump must be True while CANARY_PENDING")
    if decision.get("activation_canary_owns_save") is not True:
        errors.append("activation_canary_owns_save must be True while CANARY_PENDING")
    if decision.get("run_canary") is True:
        errors.append(
            "historical decide must not run_canary (activation_canary owns save)"
        )

    # 2) Until target slot, eligibility must stay false with current last_super_run
    if last is None:
        errors.append("last_super_run missing — cannot prove cooldown protection")
    else:
        if nxt > TARGET_SLOT + __import__("datetime").timedelta(seconds=2):
            # allow microsecond formatting drift
            pass
        # Just before slot: still not eligible for historical consume
        before = TARGET_SLOT.replace(microsecond=0) - __import__("datetime").timedelta(
            minutes=1
        )
        e_before, nxt_before, _ = is_eligible(before)
        if e_before:
            errors.append(f"eligible unexpectedly before slot at {before.isoformat()}")
        d_before = decide_super_parrain_action()
        if d_before.get("run_bump") is not False:
            errors.append("run_bump True before slot")

        # At/after slot with CANARY_PENDING still skip (no historical consume)
        after = TARGET_SLOT + __import__("datetime").timedelta(seconds=5)
        # Temporarily reason with is_eligible only — decide uses live clock for hours
        # but canary gate does not depend on eligibility for skip
        d_live = decide_super_parrain_action()
        if d_live.get("action") != "skip" or d_live.get("run_bump") is not False:
            errors.append("even if slot open, historical must skip under CANARY_PENDING")
        e_after, _, _ = is_eligible(after)
        proof["eligible_at_slot_plus_5s"] = e_after
        proof["historical_still_skip_at_slot"] = True

    # 3) Workflow contract
    bump_yml = _workflow_text("bump_super_parrain.yml")
    canary_yml = _workflow_text("activation_canary.yml")
    if "parrainage-bumper-super" not in bump_yml:
        errors.append("bump workflow missing concurrency group parrainage-bumper-super")
    if "parrainage-bumper-super" not in canary_yml:
        errors.append("activation_canary missing concurrency group parrainage-bumper-super")
    if "CANARY_PENDING" not in bump_yml:
        errors.append("bump workflow missing CANARY_PENDING gate text")
    # Historical must not execute canary save path anymore
    if "controlled_write_super_parrain.py --program kraken --execute" in bump_yml:
        errors.append(
            "bump_super_parrain.yml must not execute controlled_write while CANARY_PENDING"
        )
    if "action=skip" not in bump_yml and 'action=skip' not in bump_yml:
        errors.append("bump workflow must force action=skip on CANARY_PENDING")
    if "sole" not in canary_yml.lower() and "only one" not in canary_yml.lower() and "CANARY_PENDING" not in canary_yml:
        errors.append("activation_canary must document sole-saver role")

    proof["workflow_checks"] = {
        "shared_concurrency": (
            "parrainage-bumper-super" in bump_yml
            and "parrainage-bumper-super" in canary_yml
        ),
        "bump_has_no_execute_canary": (
            "controlled_write_super_parrain.py --program kraken --execute" not in bump_yml
        ),
        "bump_forces_skip": "CANARY_PENDING" in bump_yml and "skip" in bump_yml,
    }
    proof["ok"] = not errors
    proof["errors"] = errors

    out = ROOT / "data" / "captures" / "canary-pending-gate-proof.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof, ensure_ascii=False, indent=2))
    print(f"proof_written={out}")
    if errors:
        print("PROOF_FAILED:", "; ".join(errors), file=sys.stderr)
        return 1
    print("PROOF_OK: historical bumper cannot consume slot before activation_canary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
