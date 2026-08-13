"""ReferralCode.tv — sequential browser writer (distinct from referralcodes.com).

Secrets: REFERRALCODE_EMAIL / REFERRALCODE_PASSWORD (no S).
Edit discovery: tools/probe_referralcode_tv_edit.py --auth
Content edit ≠ boost (#cliccami).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.inventory import list_mapping_refs
from lib.offers import OffersRepository
from lib.operator_overrides import apply_effective_to_offer
from lib.phase import content_write_allowed, phase_name
from lib.renderer import MappingRepository, Renderer, TemplateRepository

ROOT = Path(__file__).resolve().parents[2]

# Proven navigation anchors (public + bumper) — filled/extended by auth probe
KNOWN_PATHS = {
    "login": "https://www.referralcode.tv/login/",
    "listings": "https://www.referralcode.tv/my-account/?tab=listings",
    "my_account": "https://www.referralcode.tv/my-account/",
    "author_public": "https://www.referralcode.tv/author/thesuperreff/",
    "add_listing": "https://www.referralcode.tv/add-referral-code/",
    "boost_only": "#cliccami",
}


@dataclass
class ReferralCodeTvPlan:
    platform: str = "referralcode-tv"
    write_mode: str = "WRITE_PREPARED"
    method: str = "authenticated_browser_sequential"
    programs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    live: bool = False
    known_paths: dict[str, str] = field(default_factory=lambda: dict(KNOWN_PATHS))


def _load_edit_map() -> dict[str, Any]:
    path = ROOT / "data" / "captures" / "referralcode-tv-edit-map.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_write_plan(program: str | None = None) -> ReferralCodeTvPlan:
    offers = OffersRepository()
    plan = ReferralCodeTvPlan()
    plan.notes.append(
        "Distinct from referralcodes.com — secrets REFERRALCODE_EMAIL/PASSWORD."
    )
    plan.notes.append(
        f"Phase={phase_name()} content_write_allowed={content_write_allowed('referralcode-tv')}"
    )
    edit_map = _load_edit_map()
    auth = edit_map.get("auth") or {}
    public = edit_map.get("public") or {}
    if auth.get("edit_urls"):
        plan.notes.append(
            f"Auth probe found {len(auth['edit_urls'])} edit URL(s) — ready for canary wiring"
        )
        plan.write_mode = "CANARY_READY"
    else:
        plan.notes.append(
            "Auth edit URLs not yet proven. Run: "
            "python tools/probe_referralcode_tv_edit.py --auth"
        )
        plan.write_mode = "WRITE_PREPARED"
    if public.get("listing_count"):
        plan.notes.append(
            f"Public author listings mapped: {public['listing_count']} (not edit forms)"
        )

    refs = [r for r in list_mapping_refs() if r.platform == "referralcode-tv"]
    if program:
        refs = [r for r in refs if r.program == program]
    maps = MappingRepository()
    templates = TemplateRepository()
    renderer = Renderer(offers)

    # Index auth edit URLs by loose program name match later
    auth_edits = list(auth.get("edit_urls") or [])

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
                    "structure_ok": True,
                    "auth_edit_pool_size": len(auth_edits),
                }
            )
        except Exception as exc:  # noqa: BLE001
            plan.programs.append(
                {"program": ref.program, "status": "error", "error": str(exc)}
            )

    plan.live = False
    return plan


def dry_run_report(program: str | None = None) -> dict[str, Any]:
    plan = build_write_plan(program=program)
    edit_map = _load_edit_map()
    out = {
        "platform": plan.platform,
        "write_mode": plan.write_mode,
        "method": plan.method,
        "live": False,
        "known_paths": plan.known_paths,
        "programs": plan.programs,
        "notes": plan.notes,
        "pending_updates": sum(1 for p in plan.programs if p.get("changed_fields")),
        "edit_map_present": bool(edit_map),
        "auth_edit_urls": (edit_map.get("auth") or {}).get("edit_urls") or [],
        "blocker_to_write_verified": (
            None
            if plan.write_mode == "CANARY_READY"
            else "Authenticated edit URL not proven — run probe_referralcode_tv_edit.py --auth"
        )
        or "Need one successful authenticated edit + post-verify for WRITE_VERIFIED",
        "pre_canary_checklist": [
            "python tools/probe_referralcode_tv_edit.py --public",
            "python tools/probe_referralcode_tv_edit.py --auth  # with REFERRALCODE_*",
            "Confirm edit form fields (textarea/code/link) in field_probes",
            "Wire edit_url into mapping for canary program",
            "controlled canary (no #cliccami boost during content write)",
        ],
    }
    path = ROOT / "data" / "captures" / "referralcode-tv-write-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def execute_write(*_a, **_k) -> dict[str, Any]:
    return {
        "ok": False,
        "live": False,
        "write_mode": "HUMAN_SAVE_REQUIRED",
        "save_requires_captcha": True,
        "eid": "23004",
        "error": (
            "HUMAN_SAVE_REQUIRED: SAVE_REQUIRES_CAPTCHA — "
            "no bypass, no GH auto-save"
        ),
    }
