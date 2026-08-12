#!/usr/bin/env python3
"""Prepare + validate ReferralCodes.com Agent Import payload (no live commit).

Default canary: kraken only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platforms.referralcodes.agent_import import (  # noqa: E402
    DOCS_URL,
    IMPORT_UI,
    SCHEMA_VERSION,
    build_import_payload,
    validate_payload,
    write_artifacts,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--program",
        action="append",
        dest="programs",
        help="Program slug (repeatable). Default: kraken",
    )
    p.add_argument(
        "--all-mapped",
        action="store_true",
        help="Include all referralcodes mappings (not just --program)",
    )
    p.add_argument("--stem", default="referralcodes-agent-import-kraken")
    args = p.parse_args()

    if args.all_mapped:
        programs = None
        stem = "referralcodes-agent-import-all"
    else:
        programs = args.programs or ["kraken"]
        stem = args.stem
        if len(programs) == 1:
            stem = f"referralcodes-agent-import-{programs[0]}"

    payload, meta = build_import_payload(programs)
    validation = validate_payload(payload)
    paths = write_artifacts(payload, meta, stem=stem)

    print(f"docs={DOCS_URL}")
    print(f"import_ui={IMPORT_UI}")
    print(f"schema_version={SCHEMA_VERSION}")
    print(f"validation_ok={validation.ok}")
    if validation.errors:
        print("errors:")
        for e in validation.errors:
            print(f"  - {e}")
    print(f"items={len(payload.get('items') or [])}")
    for m in meta:
        print(
            f"  {m.get('program')}: {m.get('status')} "
            f"errors={m.get('errors') or []} shop={((m.get('item') or {}).get('shop'))}"
        )
    print("artifacts:")
    for k, v in paths.items():
        print(f"  {k}={v}")

    # Human-readable preview of canary JSON
    print("\n--- JSON payload ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if validation.ok and (payload.get("items")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
