#!/usr/bin/env python3
"""Applique les writes uniquement sur writers verifies / eligibles.

- Super-Parrain: si pending + eligible cooldown
- Parrainage.co: si mapping write_status pret et --allow-unverified pour 1er test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.paths import MAPPINGS_DIR
from lib.super_parrain_schedule import decide_super_parrain_action, is_eligible


def _mapping_write_status(platform: str, program: str, language: str = "fr") -> str | None:
    p = MAPPINGS_DIR / f"{platform}.{program}.{language}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("write_status")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-telegram", action="store_true")
    ap.add_argument("--program", default="kraken")
    args = ap.parse_args()

    reports = []

    # Super-Parrain: only if decision says write and eligible
    decision = decide_super_parrain_action()
    eligible, nxt, hours = is_eligible()
    print(f"super-parrain decision={decision['action']} eligible={eligible} hours={hours:.2f}")
    if decision["action"] == "write" and eligible:
        from platforms.super_parrain.writer import build_write_plan, execute_write
        from lib.super_parrain_schedule import mark_pending_done, record_super_action_now
        from lib.paths import golden_path, mapping_path

        plan = build_write_plan("super-parrain", args.program, "fr")
        if plan.structure_preserved and plan.changed_fields:
            result = asyncio.run(execute_write(plan, dry_run=False))
            reports.append({"platform": "super-parrain", "ok": result.ok, "post_match": result.post_match, "error": result.error})
            if result.ok and result.post_match:
                golden_path("super-parrain", args.program, "fr").write_bytes(plan.rendered.encode("utf-8"))
                mp = mapping_path("super-parrain", args.program, "fr")
                d = json.loads(mp.read_text(encoding="utf-8"))
                d["write_status"] = "WRITE_VERIFIED"
                d["platform_values"] = {k: plan.variables.get(k) for k in plan.mutable_fields if plan.variables.get(k)}
                mp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                mark_pending_done("super-parrain", args.program, "fr")
                record_super_action_now()
        else:
            reports.append({"platform": "super-parrain", "ok": True, "note": "noop_or_structure"})
    else:
        reports.append({
            "platform": "super-parrain",
            "ok": False,
            "skipped": True,
            "reason": decision.get("reason"),
            "next_eligible_at": decision.get("next_eligible_at"),
        })

    # Parrainage.co: only if already WRITE_VERIFIED (never bulk auto on first run)
    st = _mapping_write_status("parrainage-co", args.program, "fr")
    if st == "WRITE_VERIFIED":
        from platforms.parrainage_co.writer import build_write_plan as bp, execute_write as ew
        from lib.paths import golden_path, mapping_path

        plan = bp("parrainage-co", args.program, "fr")
        if plan.structure_preserved and plan.changed_fields:
            result = asyncio.run(ew(plan, dry_run=False))
            reports.append({"platform": "parrainage-co", "ok": result.ok, "post_match": result.post_match, "error": result.error})
            if result.ok and result.post_match:
                golden_path("parrainage-co", args.program, "fr").write_bytes(plan.rendered.encode("utf-8"))
        else:
            reports.append({"platform": "parrainage-co", "ok": True, "note": "noop"})
    else:
        reports.append({
            "platform": "parrainage-co",
            "skipped": True,
            "reason": f"write_status={st} (need WRITE_VERIFIED for auto)",
        })

    out = ROOT / "data" / "captures" / "verified-writers-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
