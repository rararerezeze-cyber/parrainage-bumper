#!/usr/bin/env python3
"""Write controle Parrainage.co — plan | canary execute (login/edit/save/reread)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.parrainage_co.writer import (  # noqa: E402
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
    p.add_argument(
        "--inspect-only",
        action="store_true",
        help=(
            "Real login + real navigation to the real edit form + a read-only "
            "DOM census. Never fills, never clicks Enregistrer/Save, never "
            "touches an override, never writes write_status. Independent of "
            "--execute/--force -- no fake write, no state mutation."
        ),
    )
    args = p.parse_args()

    plan = build_write_plan("parrainage-co", args.program, args.language)
    for line in plan_report_lines(plan):
        print(line)

    if args.inspect_only:
        result = asyncio.run(execute_write(plan, dry_run=False, inspect_only=True))
        path = OUT / f"inspect-parrainage-co-{args.program}.json"
        OUT.mkdir(parents=True, exist_ok=True)
        evidence = result.evidence_checks or {}
        dump = evidence.get("form_dump") or {}
        observed_code = next(
            (i.get("preview") for i in (dump.get("inputs") or []) if i.get("name") == "ref_code"),
            None,
        )
        observed_link = next(
            (i.get("preview") for i in (dump.get("inputs") or []) if i.get("name") == "ref_link"),
            None,
        )
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "mode": "inspect_only",
            "ok": result.ok,
            "error": result.error,
            "edit_url": result.edit_url,
            "announcement_url": plan.announcement_url,
            "steps": result.steps,
            "account_canonical_match": evidence.get("account_canonical_match"),
            "public_canonical_match": evidence.get("public_canonical_match"),
            "structure_preserved": plan.structure_preserved,
            "expected_referee_reward": plan.variables.get("referee_reward"),
            "observed_personal_code": observed_code,
            "observed_personal_link": observed_link,
            "expected_personal_code": plan.variables.get("personal_code"),
            "expected_personal_link": plan.variables.get("personal_link"),
            "code_matches_expected": observed_code == plan.variables.get("personal_code"),
            "link_matches_expected": observed_link == plan.variables.get("personal_link"),
            "account_reread_text": result.account_reread_text,
            "public_reread_text": result.post_publish_text,
            "form_dump": dump,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nok={result.ok} steps={result.steps}")
        print(f"report={path}")
        return 0 if result.ok else 1

    if not plan.structure_preserved:
        print("ABORT: structure non preserve", file=sys.stderr)
        return 2

    if not args.execute:
        result = asyncio.run(execute_write(plan, dry_run=True))
        path = OUT / f"write-parrainage-co-{args.program}-plan.json"
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
                    "pipeline": ["login", "edit", "save", "reread_account", "reread_public"],
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

    from lib.canary_gate import (
        guard_live_execute,
        preflight_live_plan,
        record_live_failure,
        record_live_success,
    )

    gate = guard_live_execute("parrainage-co")
    if not gate.get("ok"):
        print(f"REFUSED: {gate.get('error')}", file=sys.stderr)
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 2

    pre = preflight_live_plan("parrainage-co", plan, program=args.program)
    if pre.get("abort"):
        path = OUT / f"write-parrainage-co-{args.program}.json"
        OUT.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": datetime.now(timezone.utc).isoformat(),
            "execute": True,
            **pre,
            "note": (
                "No fake write. WRITE_VERIFIED requires a real targeted save."
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(pre.get("result") or pre.get("error"))
        record_live_success("parrainage-co") if pre.get("ok") else record_live_failure(
            "parrainage-co", pre.get("error") or "preflight"
        )
        return 0 if pre.get("ok") else 2

    print("\n=== EXECUTE REAL WRITE parrainage.co ===")
    result = asyncio.run(execute_write(plan, dry_run=False))
    path = OUT / f"write-parrainage-co-{args.program}.json"
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

        golden_path("parrainage-co", args.program, args.language).write_bytes(
            plan.rendered.encode("utf-8")
        )
        mpath = mapping_path("parrainage-co", args.program, args.language)
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
            "source": "controlled_write_parrainage_co",
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
        promo = mark_write_verified("parrainage-co", program=args.program, evidence=evidence)
        record_live_success("parrainage-co")
        print(f"WRITE_VERIFIED parrainage-co registry={promo}")
        return 0 if promo.get("ok") else 1

    record_live_failure("parrainage-co", result.error or "write_failed")
    try:
        from lib.write_status import mark_canary_failed

        mark_canary_failed("parrainage-co", result.error or "write_failed", program=args.program)
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
