#!/usr/bin/env python3
"""Orchestrateur de synchronisation de contenu (dry-run uniquement pour publication)."""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from lib.inventory import list_mapping_refs, list_platforms
from lib.models import DryRunResult
from lib.renderer import MappingRepository
from platforms.registry import ALL_PLATFORMS, get_adapter, normalize_platform


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronisation de contenu multi-plateformes")
    parser.add_argument("--platform", help="Identifiant plateforme, ex. super-parrain")
    parser.add_argument("--program", help="Slug programme, ex. kraken")
    parser.add_argument("--language", default="fr", help="Code langue, ex. fr")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Dry-run sur tous les mappings + plateformes MANUAL connues",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare/rend sans aucune publication reelle",
    )
    parser.add_argument(
        "--list-platforms",
        action="store_true",
        help="Liste les 7 plateformes et leur capacite",
    )
    return parser


def _print_result(result: DryRunResult, verbose: bool = True) -> None:
    if verbose:
        for line in result.to_report_lines():
            print(line)
        print("-" * 40)
    else:
        fields = ",".join(result.changed_fields.keys()) if result.changed_fields else "-"
        print(
            f"{result.platform:18} {result.program:16} {result.language:3} "
            f"{result.status:16} fields={fields}"
        )


def run_one(platform: str, program: str, language: str) -> DryRunResult:
    mapping = MappingRepository().load(platform, program, language)
    adapter = get_adapter(platform)
    return adapter.dry_run(mapping)


def run_all() -> list[DryRunResult]:
    results: list[DryRunResult] = []
    refs = list_mapping_refs()
    seen_platforms = set()

    for ref in refs:
        seen_platforms.add(ref.platform)
        try:
            result = run_one(ref.platform, ref.program, ref.language)
        except Exception as exc:  # noqa: BLE001
            result = DryRunResult(
                platform=ref.platform,
                program=ref.program,
                language=ref.language,
                sync_mode="REVIEW",
                status="error",
                historical_text=None,
                rendered_text=None,
                error=str(exc),
                blocking=False,
            )
        results.append(result)

    # Plateformes sans mapping encore (ex. MANUAL) : une ligne informative
    for pid in ALL_PLATFORMS:
        if pid in seen_platforms:
            continue
        adapter = get_adapter(pid)
        cap = getattr(adapter, "capability", "AUTO")
        if cap == "MANUAL":
            results.append(
                DryRunResult(
                    platform=pid,
                    program="*",
                    language="*",
                    sync_mode="manual_review_required",
                    status="manual",
                    historical_text=None,
                    rendered_text=None,
                    error="aucun mapping encore — plateforme MANUAL",
                    blocking=False,
                )
            )
        else:
            results.append(
                DryRunResult(
                    platform=pid,
                    program="*",
                    language="*",
                    sync_mode="REVIEW",
                    status="no_mappings",
                    historical_text=None,
                    rendered_text=None,
                    error="aucun mapping capture pour cette plateforme",
                    blocking=False,
                )
            )
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_platforms:
        for p in list_platforms():
            print(f"{p['id']:20} {p['capability']}")
        return 0

    if not args.dry_run:
        print(
            "Seul --dry-run est autorise pour l'instant (aucune publication reelle).",
            file=sys.stderr,
        )
        return 2

    if args.all:
        results = run_all()
        counts = Counter(r.status for r in results)
        for r in results:
            _print_result(r, verbose=False)
        print("=" * 40)
        print("Resume dry-run --all")
        for status, n in sorted(counts.items()):
            print(f"  {status}: {n}")
        print(f"  total: {len(results)}")
        # exit 0 even with pending_update — c'est l'etat normal a synchroniser
        # exit 1 only if technical errors dominate and zero ready mappings?
        tech = sum(1 for r in results if r.status in {"error", "render_error"})
        return 1 if tech and tech == len(results) else 0

    if not args.platform or not args.program:
        print("--platform et --program requis (ou utilisez --all)", file=sys.stderr)
        return 2

    platform = normalize_platform(args.platform)
    result = run_one(platform, args.program, args.language)
    for line in result.to_report_lines():
        print(line)
    # pending_update / in_sync / manual → 0 ; erreurs techniques → 1
    if result.status in {"render_error", "error", "missing_offer"}:
        return 1
    if result.status == "missing_source":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
