#!/usr/bin/env python3
"""Write controle Super-Parrain.

Usage:
  python tools/controlled_write_super_parrain.py --program kraken
  python tools/controlled_write_super_parrain.py --program kraken --execute

Par defaut: affiche le plan/diff uniquement (aucune publication).
--execute: publication reelle + relecture publique caractere par caractere.
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

from platforms.super_parrain.writer import (  # noqa: E402
    WriteResult,
    build_write_plan,
    execute_write,
    plan_report_lines,
)

OUT_DIR = ROOT / "data" / "captures"


def _save_result(result: WriteResult, execute: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "execute": execute,
        "ok": result.ok,
        "error": result.error,
        "edit_url": result.edit_url,
        "post_match": result.post_match,
        "steps": result.steps,
        "changed_fields": result.plan.changed_fields,
        "structure_preserved": result.plan.structure_preserved,
        "announcement_url": result.plan.announcement_url,
        "historical": result.plan.historical,
        "rendered": result.plan.rendered,
        "post_publish_text": result.post_publish_text,
    }
    path = OUT_DIR / f"write-{result.plan.platform}-{result.plan.program}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", default="kraken")
    parser.add_argument("--language", default="fr")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Publication reelle (sinon plan/diff only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Requis avec --execute pour confirmer l'ecriture reelle",
    )
    args = parser.parse_args()

    plan = build_write_plan("super-parrain", args.program, args.language)
    for line in plan_report_lines(plan):
        print(line)

    if not plan.structure_preserved:
        print("\nABORT: structure non preserve — aucune ecriture.", file=sys.stderr)
        return 2

    if not args.execute:
        result = asyncio.run(execute_write(plan, dry_run=True))
        path = _save_result(result, execute=False)
        print(f"\n[plan only] report: {path}")
        print("Relancer avec --execute --force pour publier.")
        return 0

    if not args.force:
        print("\n--execute requiert --force (confirmation explicite).", file=sys.stderr)
        return 2

    print("\n=== EXECUTE REAL WRITE ===")
    result = asyncio.run(execute_write(plan, dry_run=False))
    path = _save_result(result, execute=True)
    print(f"ok={result.ok}")
    print(f"steps={result.steps}")
    print(f"post_match={result.post_match}")
    if result.error:
        print(f"error={result.error}", file=sys.stderr)
    if result.post_publish_text is not None and not result.post_match:
        print("--- post publish (unexpected) ---")
        print(result.post_publish_text)
        print("--- expected ---")
        print(plan.rendered)
    print(f"report: {path}")

    # Update golden + platform_values on success so future dry-runs reflect published
    if result.ok and result.post_match:
        from lib.paths import golden_path, mapping_path

        gpath = golden_path("super-parrain", args.program, args.language)
        gpath.write_bytes(plan.rendered.encode("utf-8"))
        mpath = mapping_path("super-parrain", args.program, args.language)
        data = json.loads(mpath.read_text(encoding="utf-8"))
        data["platform_values"] = {
            k: plan.variables.get(k)
            for k in plan.mutable_fields
            if plan.variables.get(k) is not None
        }
        data["last_write_at"] = datetime.now(timezone.utc).isoformat()
        data["last_write_ok"] = True
        mpath.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("golden + platform_values mis a jour apres write reussi")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
