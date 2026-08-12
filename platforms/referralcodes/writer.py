"""ReferralCodes.com — prefer official import/API over browser.

BASE phase: inventory + dry-run plan only. No live publish.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.phase import live_writes_enabled, phase_name
from lib.renderer import MappingRepository

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class OfficialImportPlan:
    platform: str = "referralcodes"
    method: str = "official_import_or_manual"
    prefer: list[str] = field(
        default_factory=lambda: [
            "Agent Import",
            "official API if available",
            "JSON/CSV export-import",
            "Playwright only if official path impossible",
        ]
    )
    programs: list[dict[str, Any]] = field(default_factory=list)
    write_mode: str = "MANUAL_WRITE"
    live: bool = False
    notes: list[str] = field(default_factory=list)


def build_official_import_plan() -> OfficialImportPlan:
    """Build dry-run view of what an official import would update."""
    offers = OffersRepository()
    plan = OfficialImportPlan()
    plan.notes.append(
        "ReferralCodes.com != ReferralCode.tv. Use REFERRALCODES_* secrets only."
    )
    plan.notes.append(
        f"Phase={phase_name()} live_writes={live_writes_enabled()} — no live import."
    )

    mapped = [r for r in list_mapping_refs() if r.platform == "referralcodes"]
    repo = MappingRepository()
    for ref in mapped:
        try:
            m = repo.load(ref.platform, ref.program, ref.language)
            offer = offers.get_by_slug(ref.program)
        except Exception as exc:  # noqa: BLE001
            plan.programs.append(
                {"program": ref.program, "status": "error", "error": str(exc)}
            )
            continue
        pv = m.platform_values or {}
        desired = {
            "code": offer.get("code"),
            "link": offer.get("link"),
            "reward": offer.get("reward"),
        }
        changed = {}
        for logical, offer_key in (
            ("personal_code", "code"),
            ("personal_link", "link"),
            ("referee_reward", "reward"),
        ):
            old = pv.get(logical)
            new = desired.get(offer_key)
            if old and new and str(old) != str(new):
                changed[logical] = {"old": old, "new": new}
            elif not old and new:
                changed[logical] = {"old": None, "new": new}
        plan.programs.append(
            {
                "program": ref.program,
                "announcement_url": m.announcement_url,
                "changed_fields": changed,
                "action": "WOULD_IMPORT_UPDATE" if changed else "IN_SYNC_OR_UNKNOWN",
            }
        )

    # Capability: without confirmed Agent Import API endpoint, stay MANUAL
    plan.write_mode = "MANUAL_WRITE"
    plan.notes.append(
        "No public Agent Import API schema confirmed in-repo; keep MANUAL_WRITE "
        "until operator validates official import format. Dry-run lists desired field diffs only."
    )
    return plan


def dry_run_report() -> dict[str, Any]:
    plan = build_official_import_plan()
    out = {
        "platform": plan.platform,
        "method": plan.method,
        "prefer": plan.prefer,
        "write_mode": plan.write_mode,
        "live": False,
        "programs": plan.programs,
        "notes": plan.notes,
        "pending_updates": sum(
            1 for p in plan.programs if p.get("changed_fields")
        ),
    }
    path = ROOT / "data" / "captures" / "referralcodes-official-dry-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
