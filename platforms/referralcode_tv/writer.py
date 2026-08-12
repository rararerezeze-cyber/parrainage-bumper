"""ReferralCode.tv — sequential browser writer candidate (WRITE_PREPARED).

Distinct from referralcodes.com. Uses REFERRALCODE_* secrets (no S).
No CAPTCHA/OAuth bypass. Live publish blocked until one authenticated edit verified.
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
from lib.renderer import MappingRepository, Renderer, TemplateRepository

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ReferralCodeTvPlan:
    platform: str = "referralcode-tv"
    write_mode: str = "WRITE_PREPARED"
    method: str = "authenticated_browser_sequential"
    programs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    live: bool = False


def build_write_plan(program: str | None = None) -> ReferralCodeTvPlan:
    offers = OffersRepository()
    plan = ReferralCodeTvPlan()
    plan.notes.append("Distinct from referralcodes.com — secrets REFERRALCODE_EMAIL/PASSWORD.")
    plan.notes.append(
        f"Phase={phase_name()} live_writes={live_writes_enabled('referralcode-tv')}"
    )
    plan.notes.append(
        "WRITE_PREPARED: template render + field diffs ready. "
        "WRITE_VERIFIED requires one successful authenticated edit + post-verify."
    )

    refs = [r for r in list_mapping_refs() if r.platform == "referralcode-tv"]
    if program:
        refs = [r for r in refs if r.program == program]
    maps = MappingRepository()
    templates = TemplateRepository()
    renderer = Renderer(offers)

    for ref in refs:
        try:
            mapping = maps.load(ref.platform, ref.program, ref.language)
            offer = apply_effective_to_offer(
                offers.get_by_slug(ref.program), platform="referralcode-tv"
            )
            if not templates.exists(ref.platform, ref.program, ref.language):
                plan.programs.append(
                    {"program": ref.program, "status": "missing_template"}
                )
                continue
            template = templates.load_text(ref.platform, ref.program, ref.language)
            rendered = renderer.render(template, mapping, offer=offer)
            variables = renderer.build_variables(mapping, offer=offer)
            pv = mapping.platform_values or {}
            changed = {}
            for f in mapping.mutable_fields:
                old = pv.get(f)
                new = variables.get(f)
                if str(old or "") != str(new or ""):
                    changed[f] = {"old": old, "new": new}
            plan.programs.append(
                {
                    "program": ref.program,
                    "language": ref.language,
                    "changed_fields": changed,
                    "rendered_len": len(rendered),
                    "action": "WOULD_EDIT" if changed else "IN_SYNC",
                    "edit_url": mapping.edit_url,
                    "announcement_url": mapping.announcement_url,
                }
            )
        except Exception as exc:  # noqa: BLE001
            plan.programs.append(
                {"program": ref.program, "status": "error", "error": str(exc)}
            )

    plan.write_mode = "WRITE_PREPARED"
    plan.live = False
    return plan


def dry_run_report(program: str | None = None) -> dict[str, Any]:
    plan = build_write_plan(program=program)
    out = {
        "platform": plan.platform,
        "write_mode": plan.write_mode,
        "method": plan.method,
        "live": False,
        "programs": plan.programs,
        "notes": plan.notes,
        "pending_updates": sum(
            1 for p in plan.programs if p.get("changed_fields")
        ),
        "blocker_to_write_verified": (
            "Authenticated edit flow not yet proven with REFERRALCODE_* secrets "
            "in a controlled canary. Browser sequential writer is architected; "
            "no CAPTCHA bypass will be implemented."
        ),
    }
    path = ROOT / "data" / "captures" / "referralcode-tv-write-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def execute_write(*_a, **_k) -> dict[str, Any]:
    return {
        "ok": False,
        "write_mode": "WRITE_PREPARED",
        "error": "referralcode_tv_not_write_verified",
        "plan": dry_run_report(),
    }
