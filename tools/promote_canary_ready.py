#!/usr/bin/env python3
"""Promote platform(s) to CANARY_READY when pipeline is code-complete.

Checks (no live auth required):
  - build_write_plan structure_preserved for canary program
  - edit target present (edit_url or resolvable announcement)
  - execute_write is not a stub (inspect dry-run steps / module source)
  - controlled_write tool exists
  - secrets env keys documented in report
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.write_status import (  # noqa: E402
    get_platform_status,
    mark_canary_ready,
    STATUS_CANARY_READY,
    STATUS_WRITE_VERIFIED,
)

SPECS = {
    "parrainage-co": {
        "module": "platforms.parrainage_co.writer",
        "tool": "tools/controlled_write_parrainage_co.py",
        "secrets": ["PARRAINAGE_CO_EMAIL", "PARRAINAGE_CO_PASSWORD", "PARRAINAGE_CO_RM_COOKIE"],
        "notes": (
            "CANARY_READY: login (cookie or email/password) + edit + save + "
            "reread account/public. Kraken first. Not WRITE_VERIFIED until post_match."
        ),
    },
    "code-parrainage": {
        "module": "platforms.code_parrainage.writer",
        "tool": "tools/controlled_write_code_parrainage.py",
        "secrets": ["CODE_PARRAINAGE_EMAIL", "CODE_PARRAINAGE_PASSWORD"],
        "notes": (
            "CANARY_READY: login (slider solver as historical bumper) + edit + save + "
            "reread account (public if URL). Kraken first. Not WRITE_VERIFIED until post_match."
        ),
    },
}


def assess(platform: str, program: str = "kraken") -> dict:
    spec = SPECS[platform]
    mod = importlib.import_module(spec["module"])
    plan = mod.build_write_plan(platform, program, "fr")
    dry = mod.dry_run_report(program, "fr") if hasattr(mod, "dry_run_report") else {}
    tool_ok = (ROOT / spec["tool"]).exists()
    # Source must implement real login/save (not only stub message)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    live_impl = (
        "async def execute_write" in src
        and "live write not implemented" not in src
        and ("content_write_allowed" in src or "playwright" in src.lower())
        and "_login" in src
        and "reread" in src.lower()
    )
    has_edit = bool(getattr(plan, "edit_url", None) or getattr(plan, "announcement_url", None))
    checks = {
        "structure_preserved": bool(plan.structure_preserved),
        "has_edit_or_announcement_url": has_edit,
        "controlled_write_tool": tool_ok,
        "live_pipeline_implemented": live_impl,
        "dry_run_report": bool(dry),
    }
    ok = all(checks.values())
    return {
        "platform": platform,
        "program": program,
        "status_now": get_platform_status(platform),
        "checks": checks,
        "ok": ok,
        "secrets_needed": spec["secrets"],
        "changed_fields": list((plan.changed_fields or {}).keys()),
        "edit_url": getattr(plan, "edit_url", None),
        "announcement_url": getattr(plan, "announcement_url", None),
        "notes": spec["notes"],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--platform",
        action="append",
        choices=list(SPECS.keys()) + ["all"],
        default=None,
    )
    p.add_argument("--program", default="kraken")
    p.add_argument("--apply", action="store_true", help="Write CANARY_READY to registry")
    args = p.parse_args()
    platforms = list(SPECS.keys()) if not args.platform or "all" in args.platform else args.platform

    reports = []
    for plat in platforms:
        if plat == "all":
            continue
        r = assess(plat, args.program)
        reports.append(r)
        print(f"\n=== {plat} ===")
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if args.apply and r["ok"]:
            st = r["status_now"]
            if st == STATUS_WRITE_VERIFIED:
                print(f"skip apply: already {STATUS_WRITE_VERIFIED}")
            else:
                mark_canary_ready(plat, canary_program=args.program, notes=r["notes"])
                print(f"promoted → {STATUS_CANARY_READY}")
        elif args.apply and not r["ok"]:
            print("NOT promoted — checks failed", file=sys.stderr)

    out = ROOT / "data" / "captures" / "canary-ready-promotion.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nreport={out}")
    return 0 if all(r["ok"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
