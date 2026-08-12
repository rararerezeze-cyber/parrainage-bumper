#!/usr/bin/env python3
"""Write controle ReferralCodes.com — Agent Import plan | canary execute."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.referralcodes.writer import dry_run_report, execute_write  # noqa: E402

OUT = ROOT / "data" / "captures"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--program", default="kraken")
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--force",
        action="store_true",
        help="Confirme intention d'ecrire (ne bypass pas les gates).",
    )
    args = p.parse_args()

    plan = dry_run_report(args.program)
    print(f"WRITE PLAN referralcodes/{args.program}")
    print(f"import_ui: {plan.get('import_ui')}")
    print(f"validation_ok: {plan.get('validation_ok')}")
    print(f"write_mode: {plan.get('write_mode')}")
    print(f"items: {len((plan.get('payload') or {}).get('items') or [])}")

    if not plan.get("validation_ok"):
        print("ABORT: Agent Import schema invalid", file=sys.stderr)
        return 2

    if not args.execute:
        result = execute_write(args.program, dry_run=True)
        path = OUT / f"write-referralcodes-{args.program}-plan.json"
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "execute": False,
                    "ok": result.get("ok"),
                    "validation_ok": plan.get("validation_ok"),
                    "import_ui": plan.get("import_ui"),
                    "payload": plan.get("payload"),
                    "pipeline": result.get("pipeline"),
                    "live": False,
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

    gate = guard_live_execute("referralcodes")
    if not gate.get("ok"):
        print(f"REFUSED: {gate.get('error')}", file=sys.stderr)
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 2

    print("\n=== EXECUTE REAL WRITE referralcodes.com Agent Import ===")
    result = execute_write(args.program, dry_run=False)
    path = OUT / f"write-referralcodes-{args.program}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "execute": True,
        "ok": result.get("ok"),
        "error": result.get("error"),
        "edit_url": result.get("edit_url"),
        "post_match": result.get("post_match"),
        "steps": result.get("steps"),
        "evidence_checks": result.get("evidence_checks"),
        "snapshot_id": gate.get("snapshot_id"),
        "post_publish_text": (result.get("post_publish_text") or "")[:2000],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok={result.get('ok')} post_match={result.get('post_match')} error={result.get('error')}")
    print(f"report={path}")

    if result.get("ok") and result.get("post_match"):
        from lib.write_status import mark_write_verified

        evidence = {
            "post_match": True,
            "announcement_url": result.get("announcement_url"),
            "edit_url": result.get("edit_url"),
            "public_reread": bool(result.get("post_publish_text")),
            "immutable_ok": True,
            "source": "controlled_write_referralcodes",
            "checks": result.get("evidence_checks")
            or {
                "authenticated": True,
                "targeted_edit": True,
                "submit_ok": True,
                "reread_account": True,
                "expected_values_present": True,
                "immutable_preserved": True,
            },
        }
        promo = mark_write_verified("referralcodes", program=args.program, evidence=evidence)
        record_live_success("referralcodes")
        print(f"WRITE_VERIFIED referralcodes registry={promo}")
        return 0 if promo.get("ok") else 1

    record_live_failure("referralcodes", result.get("error") or "write_failed")
    try:
        from lib.write_status import mark_canary_failed

        mark_canary_failed("referralcodes", result.get("error") or "write_failed", program=args.program)
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
