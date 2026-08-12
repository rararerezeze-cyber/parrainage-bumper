#!/usr/bin/env python3
"""Cycle Super-Parrain unifie: bumper historique + Autofresh dans la meme passe.

Une sauvegarde par code promo = update eventuel + remontee.
N'execute PAS une passe writer separee + bumper.

Premier live (defaut): canary Kraken seulement.
  AUTOFRESH_MODE=canary
  AUTOFRESH_CANARY_PROGRAMS=kraken
  → les ~22 autres diffs restent en BUMP_ONLY.
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
from lib.super_parrain_policy import parse_canary_programs, policy_snapshot
from lib.super_parrain_schedule import decide_super_parrain_action, is_eligible, save_cycle_report
from lib.inventory import list_mapping_refs


def dry_precheck_report(env: dict | None = None) -> dict:
    env = env or os.environ
    canary = parse_canary_programs(env)
    need = []
    canary_need = []
    for ref in list_mapping_refs():
        if ref.platform != "super-parrain":
            continue
        diff = compare_from_mapping_platform_values(ref.program, ref.language)
        if diff.needs_update:
            item = {
                "program": ref.program,
                "changed_fields": list(diff.changed_fields.keys()),
            }
            need.append(item)
            if canary is None or ref.program in canary:
                canary_need.append(item)
    return {
        "programs_scanned": sum(
            1 for r in list_mapping_refs() if r.platform == "super-parrain"
        ),
        "need_update": need,
        "need_update_count": len(need),
        "canary_need_update": canary_need,
        "canary_need_update_count": len(canary_need),
        "policy": policy_snapshot(env),
    }


def _ensure_canary_env(env: dict) -> dict:
    """Defaut securise: canary kraken si rien n'est force."""
    env.setdefault("AUTOFRESH_SUPER", "1")
    env.setdefault("AUTOFRESH_MODE", "canary")
    env.setdefault("AUTOFRESH_CANARY_PROGRAMS", "kraken")
    return env


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--execute", action="store_true", help="Lance bumper.py fuse (1 save/code)")
    p.add_argument(
        "--force-slot",
        action="store_true",
        help="Dry pre-check hors creneau (jamais pour execute)",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Rollout complet (desactive canary) — UNIQUEMENT apres canary WRITE_VERIFIED",
    )
    args = p.parse_args()

    env_preview = _ensure_canary_env(os.environ.copy())
    if args.full:
        env_preview["AUTOFRESH_MODE"] = "full"
        env_preview.pop("AUTOFRESH_CANARY_PROGRAMS", None)

    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()
    pre = dry_precheck_report(env_preview)
    policy = pre["policy"]

    report = {
        "mode": "fused_bumper_canary" if policy.get("mode") == "canary" else "fused_bumper",
        "decision": decision,
        "eligible": eligible,
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2),
        "pre_check": pre,
        "policy": policy,
        "note": (
            "Au creneau: bumper.py ouvre chaque codes-promo/edit. "
            "Canary: seul(s) "
            f"{policy.get('canary_programs')} recoivent un prefill contenu; "
            "les autres = BUMP_ONLY. UN SEUL Enregistrer / code."
        ),
    }

    print(
        f"PRE-CHECK: scanned={pre['programs_scanned']} "
        f"need_update={pre['need_update_count']} "
        f"canary_eligible_updates={pre['canary_need_update_count']} "
        f"policy={policy}"
    )
    for item in pre["canary_need_update"]:
        print(f"  CANARY - {item['program']}: {item['changed_fields']}")
    if pre["need_update_count"] > pre["canary_need_update_count"]:
        print(
            f"  (autres diffs non canary: "
            f"{pre['need_update_count'] - pre['canary_need_update_count']} → BUMP_ONLY)"
        )

    if not args.execute:
        report["summary"] = {
            "PRE_CHECK": "OK",
            "CANARY_PROGRAMS": policy.get("canary_programs"),
            "UPDATE_IF_NEEDED": (
                f"CANARY_ONLY ({pre['canary_need_update_count']} programmes)"
                if policy.get("mode") == "canary"
                else f"DRY ({pre['need_update_count']} programmes)"
            ),
            "POST_VERIFY": "DEFERRED_TO_LIVE_CANARY",
            "BUMP_CYCLE_24H": "DRY",
            "server_actions": 0,
            "max_saves_if_executed": "≈N codes-promo (1 save/code fuse)",
            "content_writes_if_executed": pre["canary_need_update_count"],
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

    # Execute fused cycle via bumper (canary by default)
    env = _ensure_canary_env(os.environ.copy())
    if args.full:
        env["AUTOFRESH_MODE"] = "full"
        env.pop("AUTOFRESH_CANARY_PROGRAMS", None)
    env["TARGET_SITES"] = "super"
    env["AUTOFRESH_SUPER"] = "1"
    print(
        f"EXECUTE fused bumper mode={env.get('AUTOFRESH_MODE')} "
        f"canary={env.get('AUTOFRESH_CANARY_PROGRAMS', '*')} "
        f"(1 Enregistrer / code) …"
    )
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
    canary_verdict = bumper_stats.get("canary_verdict")
    write_status = bumper_stats.get("write_status")
    post_match = None
    if canary_verdict is not None:
        post_match = canary_verdict.get("post_match")

    report["bumper_returncode"] = proc.returncode
    report["bumper_stats"] = bumper_stats
    report["summary"] = {
        "PRE_CHECK": "OK",
        "CANARY_PROGRAMS": policy.get("canary_programs"),
        "UPDATE_IF_NEEDED": "FUSED_CANARY" if policy.get("mode") == "canary" else "FUSED_IN_SAVE",
        "POST_VERIFY": (
            "PASS"
            if post_match is True
            else ("FAIL" if post_match is False else ("SKIP" if proc.returncode != 0 else "NONE"))
        ),
        "CANARY_POST_MATCH": post_match,
        "WRITE_STATUS": write_status,
        "BUMP_CYCLE_24H": "OK" if proc.returncode == 0 else "FAIL",
        "server_actions": saves if saves is not None else "unknown",
        "programs_needing_update": pre["need_update_count"],
        "canary_need_update": pre["canary_need_update_count"],
        "autofresh_updated": (bumper_stats.get("autofresh") or {}).get("updated"),
        "autofresh_bump_only": (bumper_stats.get("autofresh") or {}).get("bump_only"),
        "canary_skipped": (bumper_stats.get("autofresh") or {}).get("canary_skipped"),
    }
    path = save_cycle_report(report)
    print("--- CYCLE SUMMARY ---")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report={path}")
    # Bumper ok même si canary content fail (bump historique préservé)
    # Mais code retour 2 si canary post_match=false pour signaler
    if proc.returncode != 0:
        return 1
    if post_match is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
