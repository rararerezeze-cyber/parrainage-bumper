#!/usr/bin/env python3
"""Live writes ONLY for platforms with strict WRITE_VERIFIED status.

Telegram path:
  - WRITE_VERIFIED → may update
  - CANARY_READY / WRITE_PREPARED → plan only (use tools/*_canary.py for promotion)

Never promotes to WRITE_VERIFIED without full post-verify evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.paths import MAPPINGS_DIR
from lib.write_status import (
    STATUS_WRITE_VERIFIED,
    get_platform_status,
    is_telegram_live_capable,
    summary as write_summary,
)


def _mapping_write_status(platform: str, program: str, language: str = "fr") -> str | None:
    p = MAPPINGS_DIR / f"{platform}.{program}.{language}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("write_status")


def _try_super_parrain(program: str) -> dict:
    st = get_platform_status("super-parrain")
    if st != STATUS_WRITE_VERIFIED:
        return {
            "platform": "super-parrain",
            "skipped": True,
            "reason": f"status={st} (need WRITE_VERIFIED for telegram live; use canary tool)",
            "status": st,
        }
    if not is_telegram_live_capable("super-parrain"):
        return {
            "platform": "super-parrain",
            "skipped": True,
            "reason": "not_telegram_live_capable",
        }

    from platforms.super_parrain.writer import build_write_plan, execute_write
    from lib.super_parrain_schedule import is_eligible, mark_pending_done, record_super_action_now
    from lib.paths import golden_path, mapping_path

    eligible, nxt, hours = is_eligible()
    if not eligible:
        return {
            "platform": "super-parrain",
            "skipped": True,
            "reason": "cooldown_active",
            "next_eligible_at": nxt.isoformat(),
            "hours_remaining": round(hours, 2),
        }

    plan = build_write_plan("super-parrain", program, "fr")
    if not plan.structure_preserved:
        return {"platform": "super-parrain", "ok": False, "error": "structure_not_preserved"}
    if not plan.changed_fields:
        return {"platform": "super-parrain", "ok": True, "note": "noop_in_sync"}

    result = asyncio.run(execute_write(plan, dry_run=False))
    out = {
        "platform": "super-parrain",
        "ok": result.ok,
        "post_match": result.post_match,
        "error": result.error,
        "changed_fields": plan.changed_fields,
    }
    if result.ok and result.post_match:
        golden_path("super-parrain", program, "fr").write_bytes(plan.rendered.encode("utf-8"))
        mp = mapping_path("super-parrain", program, "fr")
        d = json.loads(mp.read_text(encoding="utf-8"))
        d["platform_values"] = {
            k: plan.variables.get(k) for k in plan.mutable_fields if plan.variables.get(k)
        }
        d["write_status"] = "WRITE_VERIFIED"
        d["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mark_pending_done("super-parrain", program, "fr")
        record_super_action_now()
        out["action"] = "UPDATED_VERIFIED"
    return out


def _try_platform_if_verified(platform: str, program: str, language: str = "fr") -> dict:
    st = get_platform_status(platform)
    if st != STATUS_WRITE_VERIFIED:
        return {
            "platform": platform,
            "skipped": True,
            "reason": f"status={st} (PLAN_ONLY until WRITE_VERIFIED)",
            "status": st,
        }
    # Import platform writer dynamically when verified
    writers = {
        "parrainage-co": "platforms.parrainage_co.writer",
        "code-parrainage": "platforms.code_parrainage.writer",
        "1parrainage": "platforms.oneparrainage.writer",
    }
    mod_name = writers.get(platform)
    if not mod_name:
        return {
            "platform": platform,
            "skipped": True,
            "reason": "no_live_writer_module_or_use_specialized_canary",
            "status": st,
        }
    try:
        import importlib

        mod = importlib.import_module(mod_name)
        build = getattr(mod, "build_write_plan")
        execute = getattr(mod, "execute_write")
    except Exception as exc:  # noqa: BLE001
        return {"platform": platform, "ok": False, "error": f"import:{exc}"}

    plan = build(platform, program, language)
    if not getattr(plan, "structure_preserved", True):
        return {"platform": platform, "ok": False, "error": "structure_not_preserved"}
    if not getattr(plan, "changed_fields", None):
        return {"platform": platform, "ok": True, "note": "noop"}
    result = asyncio.run(execute(plan, dry_run=False))
    return {
        "platform": platform,
        "ok": getattr(result, "ok", False),
        "post_match": getattr(result, "post_match", None),
        "error": getattr(result, "error", None),
        "action": "UPDATED_VERIFIED" if getattr(result, "ok", False) else "FAILED",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-telegram", action="store_true")
    ap.add_argument("--program", default="kraken")
    ap.add_argument("--plan-only", action="store_true", help="Never live write")
    args = ap.parse_args()

    ws = write_summary()
    print(f"WRITE_VERIFIED={ws.get('WRITE_VERIFIED')} live_capable={ws.get('telegram_live_capable')}")

    reports: list[dict] = []
    if args.plan_only or not ws.get("write_verified_count"):
        reports.append(
            {
                "note": "no_write_verified_platforms_or_plan_only",
                "WRITE_VERIFIED": ws.get("WRITE_VERIFIED"),
                "platforms": ws.get("platforms"),
            }
        )
    else:
        reports.append(_try_super_parrain(args.program))
        for plat in ("parrainage-co", "code-parrainage", "1parrainage"):
            reports.append(_try_platform_if_verified(plat, args.program))

    out = ROOT / "data" / "captures" / "verified-writers-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "from_telegram": args.from_telegram,
        "program": args.program,
        "write_status": ws,
        "reports": reports,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
