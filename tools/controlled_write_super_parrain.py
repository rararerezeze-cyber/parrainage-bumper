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
    args = parser.parse_args()

    # Always queue pending so scheduler prefers update over bump
    enqueue_pending("super-parrain", args.program, args.language, reason="content_update")

    plan = build_write_plan("super-parrain", args.program, args.language)
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
        print("WRITE_VERIFIED super-parrain")
        return 0

    # blocked_24h from writer also counts as non-verified pending
    if result.error and "24h" in (result.error or "").lower():
        print("Pending conserve — cooldown plateforme", file=sys.stderr)
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
