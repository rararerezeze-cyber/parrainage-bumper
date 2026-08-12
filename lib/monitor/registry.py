from __future__ import annotations

import json
from pathlib import Path

from lib.monitor.models import SourceConfig
from lib.paths import DATA_DIR

REGISTRY_PATH = DATA_DIR / "offer-sources.json"


def load_registry(path: Path | None = None) -> dict[str, SourceConfig]:
    p = path or REGISTRY_PATH
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, SourceConfig] = {}
    for item in raw.get("programs") or []:
        cfg = SourceConfig(
            program=item["program"],
            source_url=item.get("source_url"),
            source_type=item.get("source_type") or "manual",
            extraction_method=item.get("extraction_method") or "none",
            fields_supported=list(item.get("fields_supported") or ["referee_reward"]),
            locale=item.get("locale") or "fr",
            auth_required=bool(item.get("auth_required")),
            parser=item.get("parser") or "generic_reward_html",
            confidence_default=item.get("confidence_default") or "REVIEW",
            notes=item.get("notes"),
            enabled=item.get("enabled", True),
        )
        out[cfg.program] = cfg
    return out


def save_registry(configs: dict[str, SourceConfig], path: Path | None = None) -> Path:
    p = path or REGISTRY_PATH
    programs = [c.to_dict() for c in sorted(configs.values(), key=lambda x: x.program)]
    payload = {
        "version": 1,
        "note": "Public official sources only. No personal codes/links. No financial account login.",
        "programs": programs,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def coverage_stats(configs: dict[str, SourceConfig], total_programs: int) -> dict:
    enabled = [c for c in configs.values() if c.enabled and c.source_url]
    by_type: dict[str, int] = {}
    for c in configs.values():
        by_type[c.source_type] = by_type.get(c.source_type, 0) + 1
    return {
        "programs_total": total_programs,
        "official_source_configured": len(enabled),
        "by_source_type": by_type,
        "deterministic_parser": sum(
            1 for c in enabled if c.parser and c.parser != "none"
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
    }
