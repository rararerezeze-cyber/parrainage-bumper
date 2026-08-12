from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from lib.monitor.models import (
    DEFAULT_FIELD_SOURCES,
    OfferKind,
    SourceClass,
    SourceConfig,
)
from lib.paths import DATA_DIR

REGISTRY_PATH = DATA_DIR / "offer-sources.json"
MAPPINGS_DIR = DATA_DIR / "platform-mappings"


def mapping_impact_counts() -> dict[str, int]:
    """Count platform-mapping files per program (~149)."""
    counts: Counter[str] = Counter()
    if not MAPPINGS_DIR.exists():
        return {}
    for path in MAPPINGS_DIR.glob("*.json"):
        # super-parrain.kraken.fr.json → program = kraken
        parts = path.stem.split(".")
        if len(parts) >= 2:
            # platform.program.lang
            program = parts[1]
        else:
            program = parts[0]
        counts[program] += 1
    return dict(counts)


def _cfg_from_dict(item: dict[str, Any], impact: dict[str, int]) -> SourceConfig:
    fs = item.get("field_sources")
    if not isinstance(fs, dict) or not fs:
        fs = dict(DEFAULT_FIELD_SOURCES)
    else:
        merged = dict(DEFAULT_FIELD_SOURCES)
        merged.update(fs)
        fs = merged
    program = item["program"]
    return SourceConfig(
        program=program,
        source_url=item.get("source_url"),
        source_type=item.get("source_type") or "manual",
        extraction_method=item.get("extraction_method") or "none",
        fields_supported=list(item.get("fields_supported") or ["referee_reward"]),
        locale=item.get("locale") or "fr",
        auth_required=bool(item.get("auth_required")),
        parser=item.get("parser") or "structured_first",
        confidence_default=item.get("confidence_default") or "REVIEW",
        notes=item.get("notes"),
        enabled=item.get("enabled", True),
        source_class=item.get("source_class") or SourceClass.UNVERIFIED.value,
        offer_kind=item.get("offer_kind") or OfferKind.PUBLIC_CAMPAIGN.value,
        field_sources=fs,
        structured_endpoint=item.get("structured_endpoint"),
        last_verify_http=item.get("last_verify_http"),
        last_verify_at=item.get("last_verify_at"),
        impact_count=int(item.get("impact_count") or impact.get(program) or 0),
    )


def load_registry(path: Path | None = None) -> dict[str, SourceConfig]:
    p = path or REGISTRY_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    impact = mapping_impact_counts()
    out: dict[str, SourceConfig] = {}
    for item in raw.get("programs") or []:
        cfg = _cfg_from_dict(item, impact)
        # always refresh impact from live mappings
        cfg.impact_count = impact.get(cfg.program, cfg.impact_count)
        out[cfg.program] = cfg
    return out


def save_registry(configs: dict[str, SourceConfig], path: Path | None = None) -> Path:
    p = path or REGISTRY_PATH
    impact = mapping_impact_counts()
    for c in configs.values():
        c.impact_count = impact.get(c.program, c.impact_count)
    programs = [c.to_dict() for c in sorted(configs.values(), key=lambda x: (-x.impact_count, x.program))]
    payload = {
        "version": 2,
        "note": (
            "Public official sources only. Observation-only. "
            "source_class is verification status, not mere URL presence. "
            "No personal codes/links. No financial account login."
        ),
        "programs": programs,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def coverage_stats(configs: dict[str, SourceConfig], total_programs: int) -> dict:
    enabled = [c for c in configs.values() if c.enabled and c.source_url]
    by_type: dict[str, int] = {}
    by_class: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for c in configs.values():
        by_type[c.source_type] = by_type.get(c.source_type, 0) + 1
        by_class[c.source_class] = by_class.get(c.source_class, 0) + 1
        by_kind[c.offer_kind] = by_kind.get(c.offer_kind, 0) + 1
    program_parsers = {
        "generic_reward_html",
        "structured_first",
        "static_canonical_hint",
        "none",
    }
    return {
        "programs_total": total_programs,
        "official_source_configured": len(enabled),
        "verified_official": sum(
            1 for c in configs.values() if c.source_class == SourceClass.VERIFIED_OFFICIAL.value
        ),
        "by_source_type": by_type,
        "by_source_class": by_class,
        "by_offer_kind": by_kind,
        "deterministic_parser": sum(
            1 for c in enabled if c.parser and c.parser not in {"none", ""}
        ),
        "program_specific_parser": sum(
            1 for c in enabled if c.parser and c.parser not in program_parsers
        ),
        "structured_or_api": sum(
            1 for c in enabled if c.source_type in {"api", "structured_json"}
        ),
        "html_sources": sum(1 for c in enabled if c.source_type in {"html", "official_page"}),
        "manual_or_unmonitorable": sum(
            1
            for c in configs.values()
            if c.source_type in {"manual", "unmonitorable"} or not c.source_url
        ),
        "browser_required": sum(
            1 for c in configs.values() if c.extraction_method == "playwright_public"
        ),
        "mappings_total": sum(mapping_impact_counts().values()),
    }


def priority_table(configs: dict[str, SourceConfig]) -> list[dict[str, Any]]:
    """Rank programs by impact (mappings) for hardening order."""
    rows = []
    for c in sorted(configs.values(), key=lambda x: (-x.impact_count, x.program)):
        rows.append(
            {
                "program": c.program,
                "mapped_announcements": c.impact_count,
                "source_class": c.source_class,
                "offer_kind": c.offer_kind,
                "parser": c.parser,
                "source_url": c.source_url,
                "enabled": c.enabled,
            }
        )
    return rows
