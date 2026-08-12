#!/usr/bin/env python3
"""Cycle Super-Parrain: PRE-CHECK Autofresh → update si besoin → verify → bumper historique.

Ne remplace pas bumper.py. Compose avec lui au creneau ~24h.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.inventory import list_mapping_refs
from lib.super_parrain_schedule import (
    decide_super_parrain_action,
    is_eligible,
    mark_pending_done,
    record_super_action_now,
    save_cycle_report,
)
from platforms.super_parrain.writer import WriteResult, build_write_plan, execute_write


def _discover_programs_needing_update() -> list[dict]:
    """Compare golden vs offers render for every super-parrain mapping."""
    needed = []
    for ref in list_mapping_refs():
        if ref.platform != "super-parrain":
            continue
        try:
            plan = build_write_plan("super-parrain", ref.program, ref.language)
        except Exception as exc:  # noqa: BLE001
            needed.append(
                {
                    "program": ref.program,
                    "language": ref.language,
                    "error": str(exc),
                    "needs_update": False,
                }
            )
            continue
        item = {
            "program": ref.program,
            "language": ref.language,
            "needs_update": bool(plan.changed_fields) and plan.structure_preserved,
            "structure_preserved": plan.structure_preserved,
            "changed_fields": plan.changed_fields,
        }
        needed.append(item)
    return needed


async def _try_updates(programs: list[dict], *, execute: bool) -> list[dict]:
    results = []
    for item in programs:
        if not item.get("needs_update"):
            results.append({**item, "update": "skip_no_change"})
            continue
        if item.get("error"):
            results.append({**item, "update": "skip_error"})
            continue
        plan = build_write_plan("super-parrain", item["program"], item["language"])
        if not execute:
            results.append(
                {
                    **item,
                    "update": "dry_run_would_update",
                    "changed_fields": plan.changed_fields,
                }
            )
            continue
        wr: WriteResult = await execute_write(plan, dry_run=False)
        entry = {
            **item,
            "update": "ok" if wr.ok and wr.post_match else "failed",
            "post_match": wr.post_match,
            "error": wr.error,
            "steps": wr.steps,
            "edit_url": wr.edit_url,
            # Si save a reussi sur codes-promo/edit, la plateforme compte souvent
            # cette action comme remontee pour CE code. On le note; le bumper
            # historique traite toujours l'ensemble des codes ensuite.
            "save_may_count_as_remount": bool(
                wr.ok and wr.edit_url and "codes-promo" in (wr.edit_url or "")
            ),
        }
        if wr.ok and wr.post_match:
            from lib.paths import golden_path, mapping_path

            golden_path("super-parrain", item["program"], item["language"]).write_bytes(
                plan.rendered.encode("utf-8")
            )
            mpath = mapping_path("super-parrain", item["program"], item["language"])
            data = json.loads(mpath.read_text(encoding="utf-8"))
            data["platform_values"] = {
                k: plan.variables.get(k)
                for k in plan.mutable_fields
                if plan.variables.get(k) is not None
            }
            data["write_status"] = "WRITE_VERIFIED"
            data["last_write_at"] = datetime.now(timezone.utc).isoformat()
            mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            mark_pending_done("super-parrain", item["program"], item["language"])
        results.append(entry)
    return results


def _run_historical_bumper() -> dict:
    """Execute bumper.py inchange (TARGET_SITES=super deja possible via env)."""
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.setdefault("TARGET_SITES", "super")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bumper.py")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
        "ok": proc.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute updates + bumper (sinon dry pre-check only)",
    )
    parser.add_argument(
        "--force-slot",
        action="store_true",
        help="Ignore le creneau 24h pour le pre-check dry-run UNIQUEMENT (jamais pour execute)",
    )
    parser.add_argument(
        "--skip-bump",
        action="store_true",
        help="Debug: pre-check/update only (ne pas utiliser en prod)",
    )
    args = parser.parse_args()

    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()

    report: dict = {
        "mode": "execute" if args.execute else "dry",
        "decision": decision,
        "eligible": eligible,
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2),
        "pre_check": None,
        "updates": [],
        "bump": None,
        "server_actions": 0,
        "summary": {},
    }

    # Hors creneau: comportement historique = rien (sauf dry-run force pour inspect)
    if not eligible and args.execute:
        report["summary"] = {
            "PRE_CHECK": "SKIP",
            "UPDATE": "SKIP",
            "POST_VERIFY": "SKIP",
            "BUMP_CYCLE_24H": "WAIT",
            "reason": "cooldown_active_like_historical_bumper",
        }
        save_cycle_report(report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"next_eligible_at={nxt.isoformat()} hours_remaining={hours:.2f}")
        return 0

    if not eligible and not args.force_slot:
        report["summary"] = {
            "PRE_CHECK": "SKIP",
            "UPDATE": "SKIP",
            "POST_VERIFY": "SKIP",
            "BUMP_CYCLE_24H": "WAIT",
        }
        save_cycle_report(report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 0

    # --- PRE-CHECK ---
    pre = _discover_programs_needing_update()
    report["pre_check"] = {
        "programs_scanned": len(pre),
        "need_update": [p for p in pre if p.get("needs_update")],
        "all": pre,
    }
    need = [p for p in pre if p.get("needs_update")]
    print(f"PRE-CHECK: scanned={len(pre)} need_update={len(need)}")
    for p in need:
        print(f"  - {p['program']}: {list((p.get('changed_fields') or {}).keys())}")

    # --- UPDATE IF NEEDED ---
    updates = asyncio.run(_try_updates(need, execute=args.execute))
    report["updates"] = updates
    updated_ok = [u for u in updates if u.get("update") == "ok"]
    updated_fail = [u for u in updates if u.get("update") == "failed"]
    would = [u for u in updates if u.get("update") == "dry_run_would_update"]
    report["server_actions"] += len(updated_ok) + len(updated_fail)

    # --- BUMP HISTORIQUE ---
    # Toujours au creneau, sauf debug --skip-bump.
    # Si un save content a deja compte comme remontee pour un code, bumper.py
    # gerera le 24h de ce code et montera les autres — comportement plateforme.
    bump_result = None
    if args.execute and not args.skip_bump:
        print("BUMP: running historical bumper.py …")
        bump_result = _run_historical_bumper()
        report["bump"] = bump_result
        report["server_actions"] += 1 if bump_result else 0
        if bump_result and bump_result.get("ok"):
            record_super_action_now()
    elif not args.execute:
        report["bump"] = {"ok": None, "note": "dry-run — bumper not executed"}
    else:
        report["bump"] = {"ok": None, "note": "skipped via --skip-bump"}
        if updated_ok:
            record_super_action_now()

    # Summary board
    pre_ok = True
    update_status = (
        "N/A"
        if not need
        else (
            "DRY"
            if not args.execute
            else ("OK" if updated_ok and not updated_fail else ("PARTIAL" if updated_ok else "FAIL"))
        )
    )
    post_ok = (
        "N/A"
        if not need
        else (
            "DRY"
            if not args.execute
            else (
                "OK"
                if updated_ok and all(u.get("post_match") for u in updated_ok)
                and not updated_fail
                else ("FAIL" if updated_fail else "OK")
            )
        )
    )
    bump_status = (
        "DRY"
        if not args.execute
        else (
            "SKIP"
            if args.skip_bump
            else ("OK" if bump_result and bump_result.get("ok") else "FAIL")
        )
    )

    report["summary"] = {
        "PRE_CHECK": "OK" if pre_ok else "FAIL",
        "UPDATE_IF_NEEDED": update_status if need else "NONE",
        "POST_VERIFY": post_ok if need else "N/A",
        "BUMP_CYCLE_24H": bump_status,
        "programs_needing_update": len(need),
        "updates_ok": len(updated_ok),
        "updates_failed": len(updated_fail),
        "would_update_dry": len(would),
        "server_actions": report["server_actions"],
    }
    path = save_cycle_report(report)
    print("--- CYCLE SUMMARY ---")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={path}")

    if args.execute and updated_fail:
        return 1
    if args.execute and bump_result is not None and not bump_result.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
