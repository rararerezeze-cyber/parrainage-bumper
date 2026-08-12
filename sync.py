#!/usr/bin/env python3
"""Orchestrateur de synchronisation de contenu (dry-run uniquement pour publication)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from lib.coverage import print_coverage, write_coverage_report
from lib.inventory import list_mapping_refs, list_platforms
from lib.models import DryRunResult
from lib.paths import DATA_DIR
from lib.renderer import MappingRepository
from platforms.registry import ALL_PLATFORMS, get_adapter, normalize_platform, platform_capability


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronisation de contenu multi-plateformes")
    parser.add_argument("--platform", help="Identifiant plateforme, ex. super-parrain")
    parser.add_argument("--program", help="Slug programme, ex. kraken")
    parser.add_argument("--language", default="fr", help="Code langue, ex. fr")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Dry-run sur tous les mappings + plateformes sans mapping",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare/rend sans aucune publication reelle",
    )
    parser.add_argument(
        "--list-platforms",
        action="store_true",
        help="Liste les 7 plateformes et leur capacite effective",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Rapport de couverture programme x plateforme",
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
            f"{result.status:18} fields={fields}"
        )


def run_one(platform: str, program: str, language: str) -> DryRunResult:
    mapping = MappingRepository().load(platform, program, language)
    adapter = get_adapter(platform)
    return adapter.dry_run(mapping)


def _needs_canonical_count() -> int:
    path = DATA_DIR / "needs_canonical_data.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data.get("items") or [])
    except Exception:
        return 0


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

    for pid in ALL_PLATFORMS:
        if pid in seen_platforms:
            continue
        cap = platform_capability(pid)
        if cap == "MANUAL":
            status = "manual"
            mode = "manual_review_required"
            err = "aucun mapping — plateforme MANUAL"
        elif cap == "CAPTURE_PENDING":
            status = "capture_pending"
            mode = "CAPTURE_PENDING"
            err = "aucun mapping reel capture — capture requise"
        else:
            status = "no_mappings"
            mode = "REVIEW"
            err = "aucun mapping"
        results.append(
            DryRunResult(
                platform=pid,
                program="*",
                language="*",
                sync_mode=mode,
                status=status,
                historical_text=None,
                rendered_text=None,
                error=err,
                blocking=False,
            )
        )

    # Ligne synthese needs_canonical_data
    ncd = _needs_canonical_count()
    if ncd:
        results.append(
            DryRunResult(
                platform="inventory",
                program="needs_canonical_data",
                language="*",
                sync_mode="REVIEW",
                status="needs_canonical_data",
                historical_text=None,
                rendered_text=None,
                error=f"{ncd} annonce(s) sans entree offers.json",
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

    if args.coverage:
        print_coverage()
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
        mapped = sum(1 for r in results if r.program != "*" and r.platform != "inventory")
        print(f"  mapped_pairs: {mapped}")
        for status in (
            "in_sync",
            "pending_update",
            "capture_pending",
            "manual",
            "needs_canonical_data",
            "render_error",
            "error",
            "missing_source",
            "no_mappings",
        ):
            if counts.get(status):
                n = counts[status]
                if status == "needs_canonical_data":
                    n = _needs_canonical_count() or n
                print(f"  {status}: {n}")
        print(f"  total_rows: {len(results)}")
        try:
            write_coverage_report()
            print("--- coverage summary ---")
            print((DATA_DIR / "coverage-report.txt").read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"(coverage report skipped: {exc})")
        tech = sum(1 for r in results if r.status in {"error", "render_error"})
        return 1 if tech and tech == len(results) else 0

    if not args.platform or not args.program:
        print("--platform et --program requis (ou utilisez --all)", file=sys.stderr)
        return 2

    platform = normalize_platform(args.platform)
    result = run_one(platform, args.program, args.language)
    for line in result.to_report_lines():
        print(line)
    if result.status in {"render_error", "error", "missing_offer", "missing_source"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
