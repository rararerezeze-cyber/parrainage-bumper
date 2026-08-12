#!/usr/bin/env python3
"""Multi-program dry-run rollout — no live writes.

Runs plan-only writers for every non-blocked platform (kraken first,
optionally all mapped programs). Super-Parrain cooldown is never forced.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.inventory import list_mapping_refs  # noqa: E402
from lib.write_status import (  # noqa: E402
    STATUS_AUTH_BLOCKED,
    STATUS_CANARY_READY,
    STATUS_WRITE_PREPARED,
    STATUS_WRITE_VERIFIED,
    get_platform_status,
)

OUT = ROOT / "data" / "captures" / "multiprogram-dry-run.json"

WRITERS = {
    "super-parrain": ("platforms.super_parrain.writer", "kraken"),
    "parrainage-co": ("platforms.parrainage_co.writer", "kraken"),
    "code-parrainage": ("platforms.code_parrainage.writer", "kraken"),
    "1parrainage": ("platforms.oneparrainage.writer", "kraken"),
    "referralcodes": ("platforms.referralcodes.writer", "kraken"),
    "referralcode-tv": ("platforms.referralcode_tv.writer", "kraken"),
}


def _dry(mod_name: str, program: str) -> dict:
    mod = importlib.import_module(mod_name)
    if hasattr(mod, "dry_run_report"):
        try:
            return mod.dry_run_report(program)
        except TypeError:
            return mod.dry_run_report(program=program)
    plan = mod.build_write_plan(program=program)
    return {
        "program": program,
        "structure_preserved": getattr(plan, "structure_preserved", None),
        "changed_fields": getattr(plan, "changed_fields", None)
        or getattr(plan, "programs", None),
        "live": False,
    }


def run(*, all_programs: bool = False) -> dict:
    rows = []
    ready_ok = True
    for plat, (mod_name, canary) in WRITERS.items():
        st = get_platform_status(plat)
        programs = [canary]
        if all_programs:
            extra = sorted(
                {
                    r.program
                    for r in list_mapping_refs()
                    if r.platform == plat and r.language in {"fr", "en"}
                }
            )
            programs = extra or programs
        plat_row: dict = {
            "platform": plat,
            "status": st,
            "blocked": st == STATUS_AUTH_BLOCKED,
            "programs": [],
            "ok": True,
        }
        if st == STATUS_AUTH_BLOCKED:
            plat_row["note"] = "skipped AUTH_BLOCKED"
            rows.append(plat_row)
            continue
        for prog in programs:
            try:
                report = _dry(mod_name, prog)
                err = report.get("error") if isinstance(report, dict) else None
                structure = True
                if isinstance(report, dict):
                    if "structure_preserved" in report:
                        structure = bool(report.get("structure_preserved"))
                    elif report.get("programs"):
                        structure = all(
                            p.get("structure_ok", True) or p.get("status") == "error"
                            for p in report["programs"]
                            if isinstance(p, dict)
                        )
                item = {
                    "program": prog,
                    "ok": err is None and structure,
                    "structure_preserved": structure,
                    "action": (report or {}).get("action") if isinstance(report, dict) else None,
                    "error": err,
                    "live": False,
                }
                plat_row["programs"].append(item)
                if not item["ok"]:
                    plat_row["ok"] = False
                    ready_ok = False
            except Exception as exc:  # noqa: BLE001
                plat_row["programs"].append(
                    {"program": prog, "ok": False, "error": str(exc), "live": False}
                )
                plat_row["ok"] = False
                ready_ok = False
        if st not in {STATUS_CANARY_READY, STATUS_WRITE_VERIFIED, STATUS_WRITE_PREPARED}:
            plat_row["ok"] = False
            ready_ok = False
        rows.append(plat_row)

    non_blocked = [r for r in rows if not r.get("blocked")]
    all_canary = all(
        r["status"] in {STATUS_CANARY_READY, STATUS_WRITE_VERIFIED} for r in non_blocked
    )
    out = {
        "at": datetime.now(timezone.utc).isoformat(),
        "live": False,
        "all_programs": all_programs,
        "platforms": rows,
        "ALL_NON_BLOCKED_PLATFORMS_CANARY_READY": "YES" if all_canary else "NO",
        "MULTIPROGRAM_DRY_RUN_READY": "YES" if ready_ok else "NO",
        "note": "Dry-run only. Super-Parrain cooldown untouched. No execute.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--all-programs", action="store_true")
    args = p.parse_args()
    out = run(all_programs=args.all_programs)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"report={OUT}")
    print(
        f"ALL_NON_BLOCKED_PLATFORMS_CANARY_READY={out['ALL_NON_BLOCKED_PLATFORMS_CANARY_READY']}"
    )
    print(f"MULTIPROGRAM_DRY_RUN_READY={out['MULTIPROGRAM_DRY_RUN_READY']}")
    return 0 if out["MULTIPROGRAM_DRY_RUN_READY"] == "YES" else 1


if __name__ == "__main__":
    raise SystemExit(main())
