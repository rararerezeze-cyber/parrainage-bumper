#!/usr/bin/env python3
"""Cycle Super-Parrain unifie: bumper historique + Autofresh dans la meme passe.

Une sauvegarde par code promo = update eventuel + remontee.
N'execute PAS une passe writer separee + bumper.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_content import compare_from_mapping_platform_values
from lib.super_parrain_schedule import decide_super_parrain_action, is_eligible, save_cycle_report
from lib.inventory import list_mapping_refs


def dry_precheck_report() -> dict:
    need = []
    for ref in list_mapping_refs():
        if ref.platform != "super-parrain":
            continue
        diff = compare_from_mapping_platform_values(ref.program, ref.language)
        if diff.needs_update:
            need.append(
                {
                    "program": ref.program,
                    "changed_fields": list(diff.changed_fields.keys()),
                }
            )
    return {
        "programs_scanned": sum(
            1 for r in list_mapping_refs() if r.platform == "super-parrain"
        ),
        "need_update": need,
        "need_update_count": len(need),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Lance bumper.py fuse (1 save/code)")
    p.add_argument(
        "--force-slot",
        action="store_true",
        help="Dry pre-check hors creneau (jamais pour execute)",
    )
    args = p.parse_args()

    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()
    pre = dry_precheck_report()

    report = {
        "mode": "fused_bumper",
        "decision": decision,
        "eligible": eligible,
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2),
        "pre_check": pre,
        "note": (
            "Au creneau: bumper.py ouvre chaque codes-promo/edit, "
            "Autofresh prefill si diff, UN SEUL Enregistrer = update+remonte. "
            "Cible ~N saves pour N codes (pas N_update + N_bump)."
        ),
    }

    print(
        f"PRE-CHECK: scanned={pre['programs_scanned']} "
        f"need_update={pre['need_update_count']}"
    )
    for item in pre["need_update"]:
        print(f"  - {item['program']}: {item['changed_fields']}")

    if not args.execute:
        report["summary"] = {
            "PRE_CHECK": "OK",
            "UPDATE_IF_NEEDED": f"DRY ({pre['need_update_count']} programmes)",
            "POST_VERIFY": "DEFERRED_TO_LIVE_CYCLE",
            "BUMP_CYCLE_24H": "DRY",
            "server_actions": 0,
            "max_saves_if_executed": "≈N codes-promo (1 save/code fuse)",
        }
        if not eligible and not args.force_slot:
            report["summary"]["BUMP_CYCLE_24H"] = "WAIT"
        path = save_cycle_report(report)
        print("--- CYCLE SUMMARY ---")
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"report={path}")
        return 0

    if not eligible:
        report["summary"] = {
            "PRE_CHECK": "SKIP",
            "UPDATE_IF_NEEDED": "SKIP",
            "POST_VERIFY": "SKIP",
            "BUMP_CYCLE_24H": "WAIT",
            "server_actions": 0,
            "reason": "hors_creneau_24h",
        }
        path = save_cycle_report(report)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"report={path}")
        return 0

    # Execute fused cycle via bumper (AUTOFRESH_SUPER=1 by default)
    env = os.environ.copy()
    env["TARGET_SITES"] = "super"
    env["AUTOFRESH_SUPER"] = "1"
    print("EXECUTE fused bumper (1 Enregistrer / code) …")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bumper.py")],
        cwd=str(ROOT),
        env=env,
    )
    # Merge bumper autofresh stats if written
    cycle_path = ROOT / "data" / "captures" / "super-parrain-last-cycle.json"
    bumper_stats = {}
    if cycle_path.exists():
        try:
            bumper_stats = json.loads(cycle_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    saves = bumper_stats.get("saves")
    report["bumper_returncode"] = proc.returncode
    report["bumper_stats"] = bumper_stats
    report["summary"] = {
        "PRE_CHECK": "OK",
        "UPDATE_IF_NEEDED": "FUSED_IN_SAVE" if pre["need_update_count"] else "NONE",
        "POST_VERIFY": "OBSERVE_ON_FIRST_LIVE" if proc.returncode == 0 else "FAIL",
        "BUMP_CYCLE_24H": "OK" if proc.returncode == 0 else "FAIL",
        "server_actions": saves if saves is not None else "unknown",
        "programs_needing_update": pre["need_update_count"],
        "autofresh_updated": (bumper_stats.get("autofresh") or {}).get("updated"),
        "autofresh_bump_only": (bumper_stats.get("autofresh") or {}).get("bump_only"),
    }
    path = save_cycle_report(report)
    print("--- CYCLE SUMMARY ---")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={path}")
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
