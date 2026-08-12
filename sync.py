#!/usr/bin/env python3
"""Orchestrateur de synchronisation de contenu (Phase 2 — dry-run uniquement)."""

from __future__ import annotations

import argparse
import sys

from lib.renderer import MappingRepository
from platforms.registry import get_adapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronisation de contenu multi-plateformes")
    parser.add_argument("--platform", required=True, help="Identifiant plateforme, ex. super-parrain")
    parser.add_argument("--program", required=True, help="Slug programme, ex. kraken")
    parser.add_argument("--language", default="fr", help="Code langue, ex. fr")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare/rend sans aucune publication reelle",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.dry_run:
        print("Seul --dry-run est autorise en Phase 2.", file=sys.stderr)
        return 2

    mapping = MappingRepository().load(args.platform, args.program, args.language)
    adapter = get_adapter(args.platform)
    result = adapter.dry_run(mapping)

    for line in result.to_report_lines():
        print(line)

    if result.blocking:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
