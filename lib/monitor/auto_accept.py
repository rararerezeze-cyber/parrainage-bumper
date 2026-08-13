"""Deterministic public-monitor auto-accept.

Observation → strict gates → ACCEPTED_PUBLIC_MONITOR_VALUE (not offers.json).
Default is simulation. Live apply only when phase.monitor_auto_accept is true.
Never writes platform ads from this module.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.monitor.engine import candidates_report
from lib.monitor.models import (
    BUSINESS_FIELDS,
    Observation,
    ObservationStatus,
    PUBLIC_MUTABLE_FIELDS,
)
from lib.monitor.shadow import MIN_LIVE_STREAK
from lib.operator_overrides import (
    SOURCE_ACCEPTED_MONITOR,
    SOURCE_CANONICAL,
    SOURCE_GLOBAL_OPERATOR,
    SOURCE_PLATFORM_OPERATOR,
    OperatorOverrideStore,
    load_accepted_monitor_fields,
    resolve_effective_value,
)
from lib.paths import ACCEPTED_MONITOR_FIELDS_PATH, DATA_DIR
from lib.write_status import (
    ROUTE_AUTO_ON_SAFE_DIFF,
    ROUTE_CANARY_PENDING_SKIP,
    ROUTE_HUMAN_SAVE_REQUIRED,
    ROUTE_NEVER_AUTO_COMMIT,
    human_local_command,
    runtime_route,
)

INITIAL_SCOPE = frozenset({"boursobank", "winamax", "igraal", "poulpeo", "kraken"})
NEVER_AUTO_FIELDS = frozenset({"personal_code", "personal_link"})
BLOCKED_OFFER_KINDS = frozenset({"APP_PERSONALIZED", "OPERATOR_ONLY"})
BLOCKED_MONITOR_STATUSES = frozenset(
    {
        "APP_PERSONALIZED",
        "OPERATOR_ONLY",
        "ANTI_BOT_BLOCKED",
        "NO_PUBLIC_REFERRAL_SOURCE",
        "BROKEN",
    }
)
KRAKEN_FORBIDDEN_REWARD = re.compile(
    r"20\s*€.*bitcoin|20\s*eur.*btc|20\s*€\s*btc", re.I
)
HISTORY_PATH = DATA_DIR / "monitor" / "accepted-history.jsonl"
SIM_REPORT = DATA_DIR / "captures" / "monitor-auto-accept-simulation.json"
SWITCH_KEY = "monitor_auto_accept"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def auto_accept_enabled() -> bool:
    """Single production switch: data/autofresh-phase.json monitor_auto_accept=true.

    AUTOFRESH_MONITOR_AUTO_ACCEPT=0 is an emergency off.
    """
    if os.environ.get("AUTOFRESH_MONITOR_AUTO_ACCEPT") == "0":
        return False
    try:
        from lib.phase import load_phase

        return bool(load_phase().get(SWITCH_KEY))
    except Exception:
        return False


def _fr_compatible(cand: dict[str, Any], obs: Observation | None) -> bool:
    country = (cand.get("source_country") or (obs.source_country if obs else "") or "").upper()
    locale = (cand.get("source_locale") or (obs.source_locale if obs else "") or "").lower()
    scope = (cand.get("campaign_scope") or (obs.campaign_scope if obs else "") or "").upper()
    if country in {"FR", "EU"}:
        return True
    if scope in {"FR", "EU"} and locale.startswith("fr"):
        return True
    return False


def _plausible(value: str | None) -> bool:
    if value is None:
        return False
    s = str(value).strip()
    if not s or len(s) > 500:
        return False
    if s.lower() in {"n/a", "none", "null", "todo", "tbd"}:
        return False
    return True


def evaluate_field(
    cand: dict[str, Any],
    obs: Observation | None,
    *,
    store: OperatorOverrideStore | None = None,
    accepted: dict[str, dict[str, str]] | None = None,
    scope: frozenset[str] = INITIAL_SCOPE,
) -> dict[str, Any]:
    """Decide ACCEPT / REJECT for one field candidate. Never writes."""
    program = (cand.get("program") or "").strip().lower()
    field = cand.get("field") or ""
    observed = cand.get("observed")
    reasons: list[str] = []
    decision = "ACCEPT"

    if program not in scope:
        return {
            "decision": "REJECT",
            "reasons": [f"outside_initial_scope:{program}"],
            "program": program,
            "field": field,
            "observed": observed,
        }

    if obs:
        if obs.monitor_status in BLOCKED_MONITOR_STATUSES:
            decision = "REJECT"
            reasons.append(f"blocked_monitor_status:{obs.monitor_status}")
        if obs.offer_kind in BLOCKED_OFFER_KINDS:
            decision = "REJECT"
            reasons.append(f"blocked_offer_kind:{obs.offer_kind}")
        if not obs.parser_tests_passed:
            decision = "REJECT"
            reasons.append("parser_tests_not_passed")
        if obs.confidence.value != "HIGH":
            decision = "REJECT"
            reasons.append(f"confidence_not_high:{obs.confidence.value}")
        if (obs.source_class or "") != "VERIFIED_OFFICIAL":
            decision = "REJECT"
            reasons.append(f"source_not_verified_official:{obs.source_class}")

    if field in NEVER_AUTO_FIELDS:
        decision = "REJECT"
        reasons.append("personal_field_forbidden")

    auth = cand.get("authority") or (obs.field_authority.get(field) if obs else None) or "UNKNOWN"
    if auth != "OFFICIAL_PUBLIC_MONITOR":
        decision = "REJECT"
        reasons.append(f"authority_not_official_public_monitor:{auth}")

    if field == "campaign_variant" and auth == "UNKNOWN":
        decision = "REJECT"
        reasons.append("campaign_variant_authority_unknown")

    if field not in BUSINESS_FIELDS and field not in PUBLIC_MUTABLE_FIELDS:
        decision = "REJECT"
        reasons.append("field_not_business")

    if not _fr_compatible(cand, obs):
        decision = "REJECT"
        reasons.append("locale_not_fr_compatible")

    if not _plausible(None if observed is None else str(observed)):
        decision = "REJECT"
        reasons.append("value_not_plausible")

    streak = int(cand.get("live_high_streak") or cand.get("high_streak") or (obs.live_high_streak if obs else 0) or 0)
    if streak < MIN_LIVE_STREAK:
        decision = "REJECT"
        reasons.append(f"streak_below_{MIN_LIVE_STREAK}:{streak}")

    store = store or OperatorOverrideStore()
    if program and field:
        for o in store.list_for_program(program):
            if o.field == field:
                decision = "REJECT"
                reasons.append("operator_override_lock")
                break

    if program == "kraken" and field == "referee_reward":
        decision = "REJECT"
        reasons.append("kraken_referee_reward_operator_locked")
        if observed and KRAKEN_FORBIDDEN_REWARD.search(str(observed)):
            reasons.append("kraken_forbidden_20eur_btc")

    if observed and KRAKEN_FORBIDDEN_REWARD.search(str(observed)) and field == "referee_reward":
        decision = "REJECT"
        reasons.append("forbidden_20eur_btc_token")

    accepted = accepted if accepted is not None else load_accepted_monitor_fields()
    current = resolve_effective_value(
        program,
        field,
        canonical=str(cand.get("canonical") or "") or None,
        store=store,
        accepted=accepted,
    )
    if current.source in {SOURCE_PLATFORM_OPERATOR, SOURCE_GLOBAL_OPERATOR}:
        decision = "REJECT"
        if "operator_override_lock" not in reasons:
            reasons.append(f"effective_source_operator:{current.source}")

    idempotent = False
    if current.value is not None and str(current.value).strip() == str(observed or "").strip():
        idempotent = True
        if decision == "ACCEPT":
            reasons.append("already_effective")

    return {
        "decision": decision,
        "reasons": reasons,
        "program": program,
        "field": field,
        "canonical": cand.get("canonical"),
        "observed": observed,
        "authority": auth,
        "live_high_streak": streak,
        "effective_now": current.value,
        "effective_source": current.source,
        "idempotent": idempotent,
        "source_url": cand.get("source_url"),
        "parser": cand.get("parser") or (obs.parser if obs else None),
    }


def _obs_index(observations: list[Observation]) -> dict[str, Observation]:
    return {o.program: o for o in observations}


def evaluate_all(
    observations: list[Observation],
    *,
    scope: frozenset[str] = INITIAL_SCOPE,
    store: OperatorOverrideStore | None = None,
) -> dict[str, Any]:
    store = store or OperatorOverrideStore()
    accepted_now = load_accepted_monitor_fields()
    by_prog = _obs_index(observations)
    cands = [c for c in candidates_report(observations) if c.get("program") in scope]
    # Always score Kraken referee_reward so the operator lock is visible
    # even when offers.json still holds the stale 20 € BTC canonical.
    kr = by_prog.get("kraken")
    if kr and "kraken" in scope:
        if not any(c.get("program") == "kraken" and c.get("field") == "referee_reward" for c in cands):
            cands.append(
                {
                    "program": "kraken",
                    "field": "referee_reward",
                    "canonical": (kr.canonical_fields or {}).get("referee_reward"),
                    "observed": (kr.observed_fields or {}).get("referee_reward"),
                    "source_url": kr.source_url,
                    "source_locale": kr.source_locale,
                    "source_country": kr.source_country,
                    "campaign_scope": kr.campaign_scope,
                    "authority": (kr.field_authority or {}).get("referee_reward") or "UNKNOWN",
                    "live_high_streak": kr.live_high_streak,
                    "parser": kr.parser,
                    "valid_authority": False,
                }
            )
    rows = [
        evaluate_field(
            c,
            by_prog.get(c.get("program") or ""),
            store=store,
            accepted=accepted_now,
            scope=scope,
        )
        for c in cands
    ]
    accepts = [
        r
        for r in rows
        if r["decision"] == "ACCEPT" and not r.get("idempotent")
    ]
    rejects = [r for r in rows if r["decision"] == "REJECT"]
    return {
        "at": _now(),
        "scope": sorted(scope),
        "candidates": len(cands),
        "rows": rows,
        "accepts": accepts,
        "rejects": rejects,
        "switch_enabled": auto_accept_enabled(),
    }


def _hypothetical_accepted(accepts: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    merged = {p: dict(f) for p, f in load_accepted_monitor_fields().items()}
    for r in accepts:
        merged.setdefault(r["program"], {})[r["field"]] = str(r["observed"])
    return merged


def simulate_routes(
    accepts: list[dict[str, Any]],
    *,
    store: OperatorOverrideStore | None = None,
) -> dict[str, Any]:
    """Field-level SAFE_DIFF + Hermes routes. No live write."""
    from lib.inventory import list_mapping_refs
    from lib.renderer import MappingRepository

    from lib.native_field_format import adapt_monitor_value_to_native

    store = store or OperatorOverrideStore()
    hypo = _hypothetical_accepted(accepts)
    maps = MappingRepository()
    diffs: list[dict[str, Any]] = []
    routes: dict[str, dict[str, Any]] = {}
    programs = sorted({r["program"] for r in accepts})

    for ref in list_mapping_refs():
        if ref.program not in programs:
            continue
        try:
            mapping = maps.load(ref.platform, ref.program, ref.language)
        except Exception:
            continue
        changed: dict[str, dict[str, str | None]] = {}
        published = mapping.platform_values or {}
        for field in mapping.mutable_fields:
            if field in NEVER_AUTO_FIELDS:
                continue
            offer_key = (mapping.offer_fields or {}).get(field)
            canon = None
            if offer_key or field in published:
                # platform_values are the published native span
                canon = published.get(field)
            current = resolve_effective_value(
                ref.program, field, platform=ref.platform, canonical=canon, store=store
            )
            future = resolve_effective_value(
                ref.program,
                field,
                platform=ref.platform,
                canonical=canon,
                store=store,
                accepted=hypo,
            )
            current_val = current.value
            future_val = future.value
            native = published.get(field)
            if native:
                current_val = adapt_monitor_value_to_native(field, current_val, native)
                future_val = adapt_monitor_value_to_native(field, future_val, native)
            if str(current_val or "") != str(future_val or ""):
                if future.source == SOURCE_ACCEPTED_MONITOR:
                    changed[field] = {"old": current_val, "new": future_val}

        route = runtime_route(ref.platform)
        if ref.platform == "super-parrain":
            route_label = "FUSED_UPDATE_BUMP" if changed else ROUTE_CANARY_PENDING_SKIP
        elif ref.platform == "referraldrop":
            route_label = "MANUAL"
        else:
            route_label = route
        if ref.platform not in routes:
            routes[ref.platform] = {
                "route": route_label,
                "runtime_route": route,
                "programs": [],
            }
        if changed:
            routes[ref.platform]["route"] = route_label
            diffs.append(
                {
                    "platform": ref.platform,
                    "program": ref.program,
                    "language": ref.language,
                    "changed_fields": changed,
                    "route": route_label,
                    "runtime_route": route,
                    "human_command": human_local_command(ref.platform)
                    if route == ROUTE_HUMAN_SAVE_REQUIRED
                    else None,
                }
            )
            if ref.program not in routes[ref.platform]["programs"]:
                routes[ref.platform]["programs"].append(ref.program)

    return {
        "simulated_safe_diffs": diffs,
        "simulated_platform_routes": routes,
        "auto_writers": [
            p
            for p, meta in routes.items()
            if meta["route"] == ROUTE_AUTO_ON_SAFE_DIFF and meta["programs"]
        ],
        "super_fused": any(d["platform"] == "super-parrain" for d in diffs),
        "rctv_human": any(d["route"] == ROUTE_HUMAN_SAVE_REQUIRED for d in diffs),
        "referralcodes_blocked": all(
            runtime_route("referralcodes") == ROUTE_NEVER_AUTO_COMMIT for _ in [0]
        ),
    }


def simulate(
    observations: list[Observation],
    *,
    persist_report: bool = True,
) -> dict[str, Any]:
    ev = evaluate_all(observations)
    routes = simulate_routes(ev["accepts"])
    report = {
        "mode": "SIMULATION",
        "live_writes_performed": 0,
        "auto_accept_applied": False,
        "switch": SWITCH_KEY,
        "switch_enabled": ev["switch_enabled"],
        "eligible_programs": sorted({r["program"] for r in ev["accepts"]}),
        "eligible_fields": sorted({f"{r['program']}.{r['field']}" for r in ev["accepts"]}),
        "rejected_fields_and_reasons": [
            {
                "program": r["program"],
                "field": r["field"],
                "observed": r["observed"],
                "reasons": r["reasons"],
            }
            for r in ev["rejects"]
        ],
        "kraken_operator_lock_preserved": not any(
            r["program"] == "kraken"
            and r["field"] == "referee_reward"
            and r["decision"] == "ACCEPT"
            for r in ev["rows"]
        ),
        "simulated_accepts": ev["accepts"],
        "simulated_safe_diffs": routes["simulated_safe_diffs"],
        "simulated_platform_routes": routes["simulated_platform_routes"],
        "super_fused_update_ready": routes["super_fused"],
        "rctv_human_route_ready": routes["rctv_human"],
        "referralcodes_block_preserved": routes["referralcodes_blocked"],
        "live_writes_performed_check": 0,
        "production_activation_switch": (
            f'data/autofresh-phase.json → "{SWITCH_KEY}": true'
        ),
        "remaining_monitor_work": [
            "Do not flip monitor_auto_accept without operator OK",
            "BoursoBank native spans are wired; campaign_variant stays rejected; reward_type has no native phrase",
            "Super content canary still CANARY_PENDING_SKIP until one fused live save is validated",
            "APP_PERSONALIZED stays Hermes/Telegram",
            "ReferralCodes still NEVER_AUTO_COMMIT pending support",
        ],
        "at": _now(),
    }
    if persist_report:
        SIM_REPORT.parent.mkdir(parents=True, exist_ok=True)
        SIM_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def save_accepted_fields(programs: dict[str, dict[str, str]]) -> Path:
    payload = {
        "version": 1,
        "updated_at": _now(),
        "note": (
            "Accepted public monitor values. Precedence: "
            "PLATFORM_OPERATOR > GLOBAL_OPERATOR > ACCEPTED_PUBLIC_MONITOR_VALUE > CANONICAL. "
            "Never personal_code/link."
        ),
        "programs": programs,
    }
    ACCEPTED_MONITOR_FIELDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCEPTED_MONITOR_FIELDS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return ACCEPTED_MONITOR_FIELDS_PATH


def apply_accepts(accepts: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    """Persist accepted monitor values. Requires the production switch. No platform write."""
    if not force and not auto_accept_enabled():
        return {
            "ok": False,
            "applied": False,
            "error": f"switch_off:{SWITCH_KEY}",
            "live_writes_performed": 0,
        }
    from lib.safety import snapshot_state

    snap = snapshot_state("monitor-auto-accept")
    current = {p: dict(f) for p, f in load_accepted_monitor_fields().items()}
    applied: list[dict[str, str]] = []
    for r in accepts:
        prog, field, val = r["program"], r["field"], str(r["observed"])
        if current.get(prog, {}).get(field) == val:
            continue
        current.setdefault(prog, {})[field] = val
        applied.append({"program": prog, "field": field, "value": val})
    save_accepted_fields(current)
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        for row in applied:
            fh.write(json.dumps({"at": _now(), **row, "snapshot": snap.get("id")}, ensure_ascii=False) + "\n")
    # Super fused cycle: enqueue only — existing bumper does one Enregistrer.
    if any(True for r in accepts):
        try:
            from lib.super_parrain_schedule import enqueue_pending

            for prog in {r["program"] for r in accepts}:
                enqueue_pending("super-parrain", prog, "fr", reason="monitor_auto_accept")
        except Exception:
            pass
    return {
        "ok": True,
        "applied": True,
        "count": len(applied),
        "snapshot_id": snap.get("id"),
        "live_writes_performed": 0,
        "path": str(ACCEPTED_MONITOR_FIELDS_PATH),
    }


def observations_from_last_report(path: Path | None = None) -> list[Observation]:
    from lib.monitor.models import Confidence, FailureCode, FieldChange

    p = path or (DATA_DIR / "captures" / "monitor-last-report.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    obs: list[Observation] = []
    for o in raw.get("observations") or []:
        changes = [
            FieldChange(field=c.get("field") or "*", old=c.get("old"), new=c.get("new"))
            for c in (o.get("changes") or [])
            if isinstance(c, dict)
        ]
        obs.append(
            Observation(
                program=o["program"],
                status=ObservationStatus(o["status"]),
                confidence=Confidence(o["confidence"]),
                source_url=o.get("source_url"),
                parser=o.get("parser") or "",
                detected_at=o.get("detected_at") or "",
                canonical_fields=o.get("canonical_fields") or {},
                observed_fields=o.get("observed_fields") or {},
                changes=changes,
                failure_code=FailureCode(o.get("failure_code") or "NONE"),
                source_class=o.get("source_class") or "UNVERIFIED",
                offer_kind=o.get("offer_kind") or "PUBLIC_CAMPAIGN",
                monitor_status=o.get("monitor_status") or "PUBLIC_MONITORABLE_PENDING",
                high_streak=int(o.get("high_streak") or 0),
                live_high_streak=int(o.get("live_high_streak") or o.get("high_streak") or 0),
                impact_count=int(o.get("impact_count") or 0),
                parser_tests_passed=bool(o.get("parser_tests_passed")),
                source_country=o.get("source_country") or "FR",
                source_locale=o.get("source_locale") or "fr",
                campaign_scope=o.get("campaign_scope") or "FR",
                field_authority=o.get("field_authority") or {},
            )
        )
    return obs
