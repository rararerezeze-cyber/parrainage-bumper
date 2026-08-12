#!/usr/bin/env python3
"""Write controle Parrainage.co (meme CLI que Super-Parrain)."""
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
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    plan = build_write_plan("parrainage-co", args.program, args.language)
    for line in plan_report_lines(plan):
        print(line)

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

    print("\n=== EXECUTE REAL WRITE parrainage.co ===")
    result = asyncio.run(execute_write(plan, dry_run=False))
    path = OUT / f"write-parrainage-co-{args.program}.json"
    path.write_text(
        json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "execute": True,
                "ok": result.ok,
                "error": result.error,
                "edit_url": result.edit_url,
                "post_match": result.post_match,
                "steps": result.steps,
                "changed_fields": plan.changed_fields,
                "rendered": plan.rendered,
                "post_publish_text": result.post_publish_text,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"ok={result.ok} post_match={result.post_match} error={result.error}")
    print(f"steps={result.steps}")
    print(f"report={path}")

    if result.ok and result.post_match:
        from lib.paths import golden_path, mapping_path

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
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("WRITE_VERIFIED parrainage-co")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
