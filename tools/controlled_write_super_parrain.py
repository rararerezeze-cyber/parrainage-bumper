#!/usr/bin/env python3
"""Write controle Super-Parrain. --force ne contourne JAMAIS le cooldown 24h."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.super_parrain_schedule import (  # noqa: E402
    enqueue_pending,
    is_eligible,
    mark_pending_done,
    record_super_action_now,
)
from platforms.super_parrain.writer import (  # noqa: E402
    build_write_plan,
    execute_write,
    plan_report_lines,
)

OUT_DIR = ROOT / "data" / "captures"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", default="kraken")
    parser.add_argument("--language", default="fr")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Confirme l'intention d'ecrire. NE contourne PAS le cooldown plateforme.",
    )
    parser.add_argument(
        "--only-fields",
        default="",
        help="Comma-separated mutable fields to change (others stay published).",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="FIELD=VALUE render override (repeatable). Example: referee_reward=3 €",
    )
    args = parser.parse_args()

    overrides: dict[str, str] = {}
    for raw in args.override or []:
        if "=" not in raw:
            print(f"invalid --override {raw!r} (expected FIELD=VALUE)", file=sys.stderr)
            return 2
        key, val = raw.split("=", 1)
        overrides[key.strip()] = val
    only_fields = [p.strip() for p in (args.only_fields or "").split(",") if p.strip()]

    if args.execute:
        # Queue pending so the fused cycle prefers update over bump-only.
        enqueue_pending(
            "super-parrain", args.program, args.language, reason="content_update"
        )

    plan = build_write_plan(
        "super-parrain",
        args.program,
        args.language,
        overrides=overrides or None,
        only_fields=only_fields or None,
    )
    for line in plan_report_lines(plan):
        print(line)

    if not plan.structure_preserved:
        print("\nABORT: structure non preserve", file=sys.stderr)
        return 2

    eligible, nxt, hours = is_eligible()
    print(f"\ncooldown_eligible={eligible} next_eligible_at={nxt.isoformat()} hours_remaining={hours:.2f}")
    print("note: --force ne contourne jamais le cooldown Super-Parrain")

    if not args.execute:
        result = asyncio.run(execute_write(plan, dry_run=True))
        path = OUT_DIR / f"write-{plan.platform}-{plan.program}-plan.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "execute": False,
                    "ok": True,
                    "changed_fields": plan.changed_fields,
                    "structure_preserved": plan.structure_preserved,
                    "next_eligible_at": nxt.isoformat(),
                    "hours_remaining": round(hours, 2),
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
        print("\n--execute requiert --force (confirmation intention).", file=sys.stderr)
        return 2

    if not eligible:
        print(
            f"\nABORT: cooldown actif ({hours:.2f}h restantes). "
            f"Reessayer apres {nxt.isoformat()}. --force ignore pour le cooldown.",
            file=sys.stderr,
        )
        path = OUT_DIR / f"write-{plan.platform}-{plan.program}.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "execute": True,
                    "ok": False,
                    "error": "cooldown_active",
                    "next_eligible_at": nxt.isoformat(),
                    "hours_remaining": round(hours, 2),
                    "changed_fields": plan.changed_fields,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 3

    if not plan.changed_fields:
        print(
            "NO_SAFE_DIFF: operator-validated values already match the published announcement. "
            "No fake edit. last_super_run untouched. WRITE_VERIFIED requires a real targeted save."
        )
        path = OUT_DIR / f"write-{plan.platform}-{plan.program}.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "execute": True,
                    "ok": True,
                    "result": "NO_SAFE_DIFF",
                    "post_match": None,
                    "write_status": "CANARY_READY",
                    "changed_fields": {},
                    "note": (
                        "Published Super-Parrain text already equals OPERATOR_VALIDATED "
                        "cpbrgddy / invite.kraken.com/JDNW/s5qudqe4 / 200 € en cryptomonnaies. "
                        "A no-op does not satisfy targeted_edit/submit/reread. "
                        "Do not invent a dummy field change."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    print("\n=== EXECUTE REAL WRITE ===")
    result = asyncio.run(execute_write(plan, dry_run=False))
    path = OUT_DIR / f"write-{plan.platform}-{plan.program}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "execute": True,
        "ok": result.ok,
        "error": result.error,
        "edit_url": result.edit_url,
        "post_match": result.post_match,
        "steps": result.steps,
        "changed_fields": plan.changed_fields,
        "structure_preserved": plan.structure_preserved,
        "historical": plan.historical,
        "rendered": plan.rendered,
        "post_publish_text": result.post_publish_text,
        "write_status": "WRITE_VERIFIED" if (result.ok and result.post_match) else "FAILED",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ok={result.ok} post_match={result.post_match} error={result.error}")
    print(f"steps={result.steps}")
    print(f"report={path}")

    if result.ok and result.post_match:
        from lib.paths import golden_path, mapping_path
        from lib.write_status import mark_write_verified, mark_canary_failed

        golden_path("super-parrain", args.program, args.language).write_bytes(
            plan.rendered.encode("utf-8")
        )
        mpath = mapping_path("super-parrain", args.program, args.language)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["platform_values"] = {
            k: plan.variables.get(k)
            for k in plan.mutable_fields
            if plan.variables.get(k) is not None
        }
        data["write_status"] = "WRITE_VERIFIED"
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mark_pending_done("super-parrain", args.program, args.language)
        record_super_action_now()
        # Strict registry promotion — only with complete evidence
        evidence = {
            "post_match": True,
            "announcement_url": plan.announcement_url,
            "edit_url": result.edit_url,
            "public_reread": bool(result.post_publish_text),
            "immutable_ok": plan.structure_preserved,
            "source": "controlled_write_super_parrain",
            "checks": {
                "authenticated": True,
                "targeted_edit": bool(plan.changed_fields),
                "submit_ok": True,
                "reread_account": True,
                "expected_values_present": True,
                "immutable_preserved": plan.structure_preserved,
            },
        }
        # Public reread available → mark check
        if result.post_publish_text:
            evidence["checks"]["reread_public"] = True
        promo = mark_write_verified("super-parrain", program=args.program, evidence=evidence)
        print(f"WRITE_VERIFIED super-parrain registry={promo}")
        payload["write_status"] = "WRITE_VERIFIED" if promo.get("ok") else "POST_MATCH_BUT_REGISTRY_INCOMPLETE"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if promo.get("ok") else 1

    # blocked_24h from writer also counts as non-verified pending
    if result.error and "24h" in (result.error or "").lower():
        print("Pending conserve — cooldown plateforme", file=sys.stderr)
        return 3
    try:
        from lib.write_status import mark_canary_failed

        mark_canary_failed("super-parrain", result.error or "write_failed", program=args.program)
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
