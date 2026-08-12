"""ReferralCodes.com — prefer official import/API over browser.

WRITE_PREPARED: dry-run import plan with effective operator values.
WRITE_VERIFIED: blocked until official Agent Import format validated with secrets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.operator_overrides import apply_effective_to_offer
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
    write_mode: str = "WRITE_PREPARED"
    live: bool = False
    notes: list[str] = field(default_factory=list)


def build_official_import_plan(program: str | None = None) -> OfficialImportPlan:
    """Build dry-run view of what an official import would update (operator-effective)."""
    offers = OffersRepository()
    plan = OfficialImportPlan()
    plan.notes.append(
        "ReferralCodes.com != ReferralCode.tv. Use REFERRALCODES_* secrets only."
    )
    plan.notes.append(
        f"Phase={phase_name()} live_writes={live_writes_enabled('referralcodes')}"
    )
    plan.notes.append(
        "WRITE_PREPARED: plan ready. WRITE_VERIFIED requires confirmed Agent Import "
        "schema + successful dry import of one program. No browser CAPTCHA bypass."
    )

    mapped = [r for r in list_mapping_refs() if r.platform == "referralcodes"]
    if program:
        mapped = [r for r in mapped if r.program == program]
    repo = MappingRepository()
    for ref in mapped:
        try:
            m = repo.load(ref.platform, ref.program, ref.language)
            offer = apply_effective_to_offer(offers.get_by_slug(ref.program), platform="referralcodes")
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
                "operator_effective": True,
            }
        )

    # Prepared (not verified): inventory + operator-effective diffs ready for official import
    plan.write_mode = "WRITE_PREPARED"
    plan.live = False
    if live_writes_enabled("referralcodes"):
        plan.notes.append(
            "Live writes enabled in phase but official import endpoint not verified — still no auto publish."
        )
    return plan


def dry_run_report(program: str | None = None) -> dict[str, Any]:
    plan = build_official_import_plan(program=program)
    out = {
        "platform": plan.platform,
        "method": plan.method,
        "prefer": plan.prefer,
        "write_mode": plan.write_mode,
        "live": False,
        "programs": plan.programs,
        "notes": plan.notes,
        "pending_updates": sum(1 for p in plan.programs if p.get("changed_fields")),
        "blocker_to_write_verified": (
            "Agent Import / API schema not validated with live credentials in CI. "
            "Next step: READ-ONLY login probe of /agents with REFERRALCODES_* secrets."
        ),
    }
    path = ROOT / "data" / "captures" / "referralcodes-official-dry-run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def execute_write(*_a, **_k) -> dict[str, Any]:
    """Live write blocked until WRITE_VERIFIED — returns prepared plan only."""
    return {
        "ok": False,
        "write_mode": "WRITE_PREPARED",
        "error": "referralcodes_not_write_verified_use_official_import",
        "plan": dry_run_report(),
    }
