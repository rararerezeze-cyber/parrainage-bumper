"""Cross-platform impact plan for operator overrides (dry-run + WRITE_VERIFIED gate)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lib.inventory import list_mapping_refs
from lib.models import PlatformMapping
from lib.offers import OffersRepository
from lib.operator_overrides import (
    SOURCE_CANONICAL,
    OperatorOverrideStore,
    apply_effective_to_offer,
    resolve_effective_value,
)
from lib.phase import phase_name
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.template_builder import extract_values_via_template
from lib.write_status import (
    STATUS_AUTH_BLOCKED,
    STATUS_AUTH_BLOCKED_MANUAL,
    STATUS_CANARY_READY,
    STATUS_WRITE_PREPARED,
    STATUS_WRITE_VERIFIED,
    get_platform_meta,
    get_platform_status,
    human_local_command,
    may_auto_execute_on_safe_diff,
    runtime_route,
    telegram_action_for_platform,
)
from platforms.registry import platform_capability


def platform_write_mode(platform: str) -> str:
    """Strict label from write-status registry (never invent WRITE_VERIFIED)."""
    st = get_platform_status(platform)
    if st == STATUS_WRITE_VERIFIED:
        return STATUS_WRITE_VERIFIED
    if st == STATUS_CANARY_READY:
        return STATUS_CANARY_READY
    if st in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
        return st
    if st == STATUS_WRITE_PREPARED:
        return STATUS_WRITE_PREPARED
    return st or STATUS_WRITE_PREPARED


@dataclass
class PlatformImpact:
    platform: str
    program: str
    language: str
    status: str  # in_sync | pending_update | missing_source | manual | error | skip
    write_mode: str
    can_auto_write: bool
    changed_fields: dict[str, dict[str, str | None]] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    route: str = ""
    human_command: str | None = None
    last_write_verified_at: str | None = None
    last_failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "program": self.program,
            "language": self.language,
            "status": self.status,
            "write_mode": self.write_mode,
            "can_auto_write": self.can_auto_write,
            "changed_fields": self.changed_fields,
            "field_sources": self.field_sources,
            "error": self.error,
            "route": self.route,
            "human_command": self.human_command,
            "last_write_verified_at": self.last_write_verified_at,
            "last_failure": self.last_failure,
        }


def _golden_values(mapping: PlatformMapping, golden: str, template: str) -> dict[str, str | None]:
    try:
        return extract_values_via_template(
            template, golden, mapping.mutable_fields, mapping.markers
        )
    except Exception:
        return dict(mapping.platform_values or {})


def plan_program_impact(
    program: str,
    *,
    platform_filter: str | None = None,
    store: OperatorOverrideStore | None = None,
) -> dict[str, Any]:
    """Compute effective values + per-platform diffs for a program (observation / plan only)."""
    store = store or OperatorOverrideStore()
    offers = OffersRepository()
    try:
        base_offer = offers.get_by_slug(program)
    except KeyError:
        return {"error": "unknown_program", "program": program}

    effective_global = apply_effective_to_offer(base_offer, platform=None, store=store)
    global_fields: dict[str, Any] = {}
    for logical in (
        "personal_code",
        "personal_link",
        "referee_reward",
        "referrer_reward",
        "conditions",
        "min_deposit",
        "min_spend",
        "trade_min",
        "qualification_days",
        "expiry_date",
        "reward_type",
    ):
        from lib.operator_overrides import LOGICAL_TO_OFFER

        canon_key = LOGICAL_TO_OFFER.get(logical)
        canon = base_offer.get(canon_key) if canon_key else None
        eff = resolve_effective_value(
            program, logical, platform=None, canonical=str(canon) if canon else None, store=store
        )
        if eff.value is not None or eff.source != SOURCE_CANONICAL:
            global_fields[logical] = {
                "old": canon,
                "effective": eff.value,
                "source": eff.source,
            }

    impacts: list[PlatformImpact] = []
    mappings = MappingRepository()
    templates = TemplateRepository()
    renderer = Renderer(offers)

    for ref in list_mapping_refs():
        if ref.program != program:
            continue
        if platform_filter and ref.platform != platform_filter:
            continue
        write_mode = platform_write_mode(ref.platform)
        route = runtime_route(ref.platform)
        can_auto = may_auto_execute_on_safe_diff(ref.platform)
        human_cmd = human_local_command(ref.platform)
        tg_action = telegram_action_for_platform(ref.platform)
        plat_meta = get_platform_meta(ref.platform)
        last_write_verified_at = plat_meta.get("last_write_verified_at")
        last_failure = plat_meta.get("last_failure")

        try:
            mapping = mappings.load(ref.platform, ref.program, ref.language)
        except Exception as exc:  # noqa: BLE001
            impacts.append(
                PlatformImpact(
                    platform=ref.platform,
                    program=ref.program,
                    language=ref.language,
                    status="error",
                    write_mode=write_mode,
                    can_auto_write=False,
                    error=str(exc),
                    route=route,
                    human_command=human_cmd,
                    last_write_verified_at=last_write_verified_at,
                    last_failure=last_failure,
                )
            )
            continue

        if not templates.exists(ref.platform, ref.program, ref.language):
            impacts.append(
                PlatformImpact(
                    platform=ref.platform,
                    program=ref.program,
                    language=ref.language,
                    status="missing_source",
                    write_mode=write_mode,
                    can_auto_write=False,
                    error="template_absent",
                    route=route,
                    human_command=human_cmd,
                    last_write_verified_at=last_write_verified_at,
                    last_failure=last_failure,
                )
            )
            continue

        try:
            template = templates.load_text(ref.platform, ref.program, ref.language)
            offer_plat = apply_effective_to_offer(base_offer, platform=ref.platform, store=store)
            rendered = renderer.render(template, mapping, offer=offer_plat)
            variables = renderer.build_variables(mapping, offer=offer_plat)

            hist_values: dict[str, str | None] = {}
            if templates.golden_exists(ref.platform, ref.program, ref.language):
                golden = templates.load_golden(ref.platform, ref.program, ref.language)
                hist_values = _golden_values(mapping, golden, template)
            else:
                hist_values = dict(mapping.platform_values or {})

            changed: dict[str, dict[str, str | None]] = {}
            field_sources: dict[str, str] = {}
            for f in mapping.mutable_fields:
                new_v = variables.get(f)
                old_v = hist_values.get(f)
                eff = resolve_effective_value(
                    program, f, platform=ref.platform, canonical=None, store=store
                )
                # source of effective for this platform field
                offer_key = (mapping.offer_fields or {}).get(f)
                canon = base_offer.get(offer_key) if offer_key else None
                eff2 = resolve_effective_value(
                    program,
                    f,
                    platform=ref.platform,
                    canonical=str(canon) if canon is not None else None,
                    store=store,
                )
                field_sources[f] = eff2.source
                if str(old_v or "") != str(new_v or ""):
                    changed[f] = {"old": old_v, "new": new_v}

            status = "in_sync" if not changed else "pending_update"
            cap = platform_capability(ref.platform)
            if write_mode in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
                status = "auth_blocked" if status == "pending_update" else status
            elif cap == "MANUAL" and write_mode not in {
                STATUS_WRITE_PREPARED,
                STATUS_WRITE_VERIFIED,
                STATUS_CANARY_READY,
            }:
                if status == "pending_update":
                    status = "manual"

            impacts.append(
                PlatformImpact(
                    platform=ref.platform,
                    program=ref.program,
                    language=ref.language,
                    status=status,
                    write_mode=write_mode,
                    can_auto_write=bool(can_auto and status == "pending_update"),
                    changed_fields=changed,
                    field_sources=field_sources,
                    error=(
                        None
                        if route != "AUTH_BLOCKED_MANUAL"
                        else "auth_blocked_manual"
                    ),
                    route=route,
                    human_command=human_cmd,
                    last_write_verified_at=last_write_verified_at,
                    last_failure=last_failure,
                )
            )
        except Exception as exc:  # noqa: BLE001
            impacts.append(
                PlatformImpact(
                    platform=ref.platform,
                    program=ref.program,
                    language=ref.language,
                    status="error",
                    write_mode=write_mode,
                    can_auto_write=False,
                    error=str(exc),
                    route=route,
                    human_command=human_cmd,
                    last_write_verified_at=last_write_verified_at,
                    last_failure=last_failure,
                )
            )

    from lib.write_status import summary as write_summary

    ws = write_summary()
    auto_capable = sum(
        1
        for i in impacts
        if i.write_mode in {STATUS_WRITE_VERIFIED, STATUS_WRITE_PREPARED, STATUS_CANARY_READY}
    )
    write_verified_live = sum(1 for i in impacts if i.write_mode == STATUS_WRITE_VERIFIED)

    # Real offer content the capture engine has observed diverging from the
    # curated mapping (never the ENGINE_METADATA/TARGETING noise -- see
    # lib.mapping_candidates.classify_field()). Never auto-applied here;
    # this is visibility only, exactly like list_operator_queue() itself.
    try:
        from lib.mapping_candidates import list_operator_queue

        business_divergences = [
            {
                "platform": c.get("platform"),
                "field": c.get("field"),
                "curated_value": c.get("curated_value"),
                "observed_value": c.get("observed_value"),
                "last_observed_at": c.get("last_observed_at"),
            }
            for c in list_operator_queue(
                platform=platform_filter, program=program
            )
        ]
    except Exception:
        business_divergences = []

    return {
        "program": program,
        "phase": phase_name(),
        "effective_global": {
            k: effective_global.get(k)
            for k in ("code", "link", "reward", "cond", "name")
        },
        "global_fields": global_fields,
        "platforms": [i.to_dict() for i in impacts],
        "business_divergences": business_divergences,
        "summary": {
            "platforms_mapped": len(impacts),
            "pending_update": sum(1 for i in impacts if i.status == "pending_update"),
            "in_sync": sum(1 for i in impacts if i.status == "in_sync"),
            "auto_capable_labels": auto_capable,
            "write_verified_live": write_verified_live,
            "WRITE_VERIFIED": ws.get("WRITE_VERIFIED"),
            "telegram_live_capable": ws.get("telegram_live_capable"),
            "business_divergences_pending": len(business_divergences),
        },
        "write_status": ws,
        "observation_only_default": True,
        "monitor_mode": "OBSERVATION_ONLY",
    }


def format_plan_report(
    program: str,
    field: str | None,
    old: str | None,
    new: str | None,
    plan: dict[str, Any],
    *,
    action: str = "set",
    platform: str | None = None,
    source: str | None = None,
) -> str:
    lines = []
    scope = f"PLATFORM={platform}" if platform else "GLOBAL"
    if action == "status":
        lines.append(f"{program} status ({scope})")
    elif action == "remove":
        lines.append(f"{program} override supprimé: {field} ({scope})")
    elif action == "list":
        lines.append(f"{program} overrides")
    else:
        lines.append(f"{program} {field or ''} → {new!r}  [{scope}]")
        if source:
            lines.append(f"source effective: {source}")
        if old is not None or new is not None:
            lines.append(f"effective value: {old!r} → {new!r}")

    lines.append("")
    if plan.get("error"):
        lines.append(f"ERROR: {plan['error']}")
        return "\n".join(lines)

    lines.append("Cross-platform impact:")
    for p in plan.get("platforms") or []:
        ch = p.get("changed_fields") or {}
        if ch:
            bits = ", ".join(f"{k}" for k in ch)
            diff = " | ".join(
                f"{k}: {v.get('old')!r}→{v.get('new')!r}" for k, v in list(ch.items())[:3]
            )
            lines.append(
                f"  {p['platform']:18} {p['status']:16} {p['write_mode']:28} {diff}"
            )
        else:
            lines.append(
                f"  {p['platform']:18} {p['status']:16} {p['write_mode']:28}"
            )
            if p.get("error"):
                lines.append(f"    error: {p['error']}")
        if p.get("last_write_verified_at"):
            lines.append(f"    last write verified: {p['last_write_verified_at']}")
        if p.get("last_failure"):
            lf = p["last_failure"]
            lines.append(f"    last error: {lf.get('error')} ({lf.get('at')})")

    divergences = plan.get("business_divergences") or []
    if divergences:
        lines.append("")
        lines.append(f"BUSINESS divergences detected ({len(divergences)}):")
        for d in divergences[:10]:
            lines.append(
                f"  {d.get('platform'):18} {d.get('field'):24} "
                f"curated={d.get('curated_value')!r} observed={d.get('observed_value')!r}"
            )
        if len(divergences) > 10:
            lines.append(f"  ... and {len(divergences) - 10} more (list_operator_queue)")

    s = plan.get("summary") or {}
    lines.append("")
    from lib.write_status import format_telegram_platform_lines, summary as write_summary

    lines.append("Write readiness (strict):")
    for line in format_telegram_platform_lines(plan.get("platforms")):
        lines.append(line)
    ws = write_summary()
    lines.append("")
    lines.append(
        f"Mapped: {s.get('platforms_mapped', 0)} | pending: {s.get('pending_update', 0)} | "
        f"in_sync: {s.get('in_sync', 0)}"
    )
    lines.append(
        f"WRITE_VERIFIED: {ws.get('WRITE_VERIFIED')} | "
        f"Telegram live-capable: {len(ws.get('telegram_live_capable') or [])}/7"
    )
    # Built from live state, never a hardcoded list. This line used to name
    # two platforms literally and kept doing so long after parrainage-co became
    # WRITE_VERIFIED + PC_OFF_READY, so the operator-facing summary
    # under-reported which platforms can actually auto-write.
    auto_writers = ", ".join(ws.get("telegram_live_capable") or []) or "aucune"
    lines.append(
        "Auto writers ONLY on PC_OFF_READY + real SAFE_DIFF "
        f"({auto_writers}). Human platforms never auto-dispatch."
    )
    lines.append("Monitor remains OBSERVATION_ONLY (no auto-accept).")
    return "\n".join(lines)
