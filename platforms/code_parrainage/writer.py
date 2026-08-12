"""Writer Code-Parrainage — dry-run prepare only pendant phase BASE.

Contract: fetch/render/diff/immutable check. Aucun save reel ici.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.mapping_guards import write_blocked_reason
from lib.offers import OffersRepository
from lib.phase import live_writes_enabled, phase_name
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template


@dataclass
class WritePlan:
    platform: str
    program: str
    language: str
    announcement_url: str | None
    edit_url: str | None
    historical: str
    rendered: str
    variables: dict[str, str | None]
    platform_values: dict[str, str]
    changed_fields: dict[str, dict[str, str | None]]
    structure_preserved: bool
    mutable_fields: list[str]


@dataclass
class WriteResult:
    ok: bool
    plan: WritePlan
    post_match: bool | None = None
    error: str | None = None
    steps: list[str] | None = None


def build_write_plan(
    platform: str = "code-parrainage",
    program: str = "kraken",
    language: str = "fr",
) -> WritePlan:
    mapping = MappingRepository().load(platform, program, language)
    templates = TemplateRepository()
    renderer = Renderer(OffersRepository())
    template = templates.load_text(platform, program, language)
    historical = templates.load_golden(platform, program, language)
    offer = renderer.offers.get_by_slug(program)
    variables = renderer.build_variables(mapping, offer=offer)
    rendered = renderer.render(template, mapping, offer=offer)

    hist_vals = dict(mapping.platform_values or {})
    extracted = extract_values_via_template(
        template, historical, mapping.mutable_fields, mapping.markers
    )
    for k, v in extracted.items():
        hist_vals.setdefault(k, v)

    changed: dict[str, dict[str, str | None]] = {}
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old != new:
            changed[field] = {"old": old, "new": new}

    check = historical
    for field in mapping.mutable_fields:
        old = hist_vals.get(field)
        new = variables.get(field)
        if old and new is not None and old in check:
            check = check.replace(old, new, 1)
    structure_preserved = check == rendered

    return WritePlan(
        platform=platform,
        program=program,
        language=language,
        announcement_url=mapping.announcement_url,
        edit_url=getattr(mapping, "edit_url", None),
        historical=historical,
        rendered=rendered,
        variables=variables,
        platform_values=hist_vals,
        changed_fields=changed,
        structure_preserved=structure_preserved,
        mutable_fields=list(mapping.mutable_fields),
    )


async def execute_write(plan: WritePlan, *, dry_run: bool = True) -> WriteResult:
    steps = ["plan"]
    blocked = write_blocked_reason(plan.platform, plan.program, plan.language)
    if blocked:
        return WriteResult(ok=False, plan=plan, error=f"WRITE_BLOCKED: {blocked}", steps=["blocked"])
    if not plan.structure_preserved:
        return WriteResult(ok=False, plan=plan, error="structure_not_preserved", steps=steps)
    if not plan.changed_fields:
        return WriteResult(ok=True, plan=plan, steps=["noop"], post_match=True)
    if dry_run or not live_writes_enabled("code-parrainage"):
        return WriteResult(
            ok=True,
            plan=plan,
            steps=[
                "dry-run only"
                if dry_run
                else f"LIVE_DISABLED ({phase_name()})"
            ],
            post_match=None,
        )
    return WriteResult(
        ok=False,
        plan=plan,
        error="live write not implemented for code-parrainage in this phase",
        steps=steps + ["live_not_implemented"],
    )


def dry_run_report(program: str = "kraken", language: str = "fr") -> dict[str, Any]:
    plan = build_write_plan(program=program, language=language)
    return {
        "platform": plan.platform,
        "program": plan.program,
        "structure_preserved": plan.structure_preserved,
        "changed_fields": plan.changed_fields,
        "edit_url": plan.edit_url,
        "action": "WOULD_UPDATE" if plan.changed_fields else "NOOP",
        "live": False,
    }
