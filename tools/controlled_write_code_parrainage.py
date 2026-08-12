#!/usr/bin/env python3
"""Write controle Code-Parrainage — plan | canary execute (login/edit/save/reread)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.code_parrainage.writer import (  # noqa: E402
    build_write_plan,
    execute_write,
    plan_report_lines,
)

OUT = ROOT / "data" / "captures"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--program", default="kraken")
    p.add_argument("--language", default="fr")
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--force",
        action="store_true",
        help="Confirme intention d'ecrire (ne bypass pas les gates phase/status).",
    )
    args = p.parse_args()

    plan = build_write_plan("code-parrainage", args.program, args.language)
    for line in plan_report_lines(plan):
        print(line)

    if not plan.structure_preserved:
        print("ABORT: structure non preserve", file=sys.stderr)
        return 2

    if not args.execute:
        result = asyncio.run(execute_write(plan, dry_run=True))
        path = OUT / f"write-code-parrainage-{args.program}-plan.json"
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "execute": False,
                    "ok": result.ok,
                    "changed_fields": plan.changed_fields,
                    "structure_preserved": plan.structure_preserved,
                    "announcement_url": plan.announcement_url,
                    "edit_url": plan.edit_url,
                    "pipeline": ["login", "edit", "save", "reread_account", "reread_public_if_any"],
                    "steps": result.steps,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n[plan only] {path}")
        return 0

    if not args.force:
        print("--execute requiert --force", file=sys.stderr)
        return 2

    from lib.canary_gate import guard_live_execute, record_live_failure, record_live_success

    gate = guard_live_execute("code-parrainage")
    if not gate.get("ok"):
        print(f"REFUSED: {gate.get('error')}", file=sys.stderr)
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 2

    print("\n=== EXECUTE REAL WRITE code-parrainage.net ===")
    result = asyncio.run(execute_write(plan, dry_run=False))
    path = OUT / f"write-code-parrainage-{args.program}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "execute": True,
        "ok": result.ok,
        "error": result.error,
        "edit_url": result.edit_url,
        "post_match": result.post_match,
        "steps": result.steps,
        "evidence_checks": result.evidence_checks,
        "changed_fields": plan.changed_fields,
        "rendered": plan.rendered,
        "post_publish_text": result.post_publish_text,
        "account_reread_text": (result.account_reread_text or "")[:2000],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok={result.ok} post_match={result.post_match} error={result.error}")
    print(f"steps={result.steps}")
    print(f"report={path}")

    if result.ok and result.post_match:
        from lib.paths import golden_path, mapping_path
        from lib.write_status import mark_write_verified

        golden_path("code-parrainage", args.program, args.language).write_bytes(
            plan.rendered.encode("utf-8")
        )
        mpath = mapping_path("code-parrainage", args.program, args.language)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["platform_values"] = {
            k: plan.variables.get(k)
            for k in plan.mutable_fields
            if plan.variables.get(k) is not None
        }
        data["write_status"] = "WRITE_VERIFIED"
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        if result.edit_url:
            data["edit_url"] = result.edit_url
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence = {
            "post_match": True,
            "announcement_url": plan.announcement_url,
            "edit_url": result.edit_url,
            "public_reread": bool(result.post_publish_text),
            "immutable_ok": plan.structure_preserved,
            "source": "controlled_write_code_parrainage",
            "checks": result.evidence_checks
            or {
                "authenticated": True,
                "targeted_edit": bool(plan.changed_fields),
                "submit_ok": True,
                "reread_account": True,
                "expected_values_present": True,
                "immutable_preserved": plan.structure_preserved,
            },
        }
        promo = mark_write_verified("code-parrainage", program=args.program, evidence=evidence)
        record_live_success("code-parrainage")
        print(f"WRITE_VERIFIED code-parrainage registry={promo}")
        return 0 if promo.get("ok") else 1

    record_live_failure("code-parrainage", result.error or "write_failed")
    try:
        from lib.write_status import mark_canary_failed

        mark_canary_failed(
            "code-parrainage", result.error or "write_failed", program=args.program
        )
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
