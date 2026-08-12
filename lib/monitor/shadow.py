"""SHADOW acceptance engine — scores monitor candidates, never auto-accepts.

Does not write offers.json, accepted-fields, or platform content.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.monitor.engine import candidates_report, production_readiness_report
from lib.monitor.models import Observation, ObservationStatus
from lib.paths import DATA_DIR
from lib.safety import classify_stop, load_circuits

SHADOW_DIR = DATA_DIR / "monitor"
SHADOW_PATH = SHADOW_DIR / "shadow-decisions.jsonl"
SHADOW_REPORT = DATA_DIR / "captures" / "monitor-shadow-report.json"

SHADOW_ACCEPT = "SHADOW_ACCEPT"
SHADOW_REVIEW = "SHADOW_REVIEW"
SHADOW_REJECT = "SHADOW_REJECT"

# Conservative gates — all must pass for SHADOW_ACCEPT
MIN_LIVE_STREAK = 3
MIN_IMPACT = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operator_locks_field(program: str | None, field: str | None) -> bool:
    """Operator override / OPERATOR_VALIDATED fields cannot be shadowed into canon."""
    if not program or not field:
        return False
    try:
        from lib.operator_overrides import OperatorOverrideStore

        for o in OperatorOverrideStore().list_for_program(program):
            if o.field == field:
                return True
    except Exception:
        pass
    try:
        from lib.monitor.registry import load_registry

        cfg = load_registry().get(program)
        src = (getattr(cfg, "field_sources", None) or {}).get(field)
        if src in {"OPERATOR_VALIDATED", "TELEGRAM_OPERATOR"}:
            return True
        if cfg and not cfg.has_fr_authority(field):
            return True
    except Exception:
        pass
    return False


def may_write_canonical(decision: str, *, program: str | None = None, field: str | None = None) -> tuple[bool, str]:
    """Hard rule: SHADOW never writes offers.json / accepted-fields / ads."""
    if decision != SHADOW_ACCEPT:
        return False, "only_explicit_human_accept_may_write"
    if _operator_locks_field(program, field):
        return False, "operator_validated_field_locked"
    return False, "shadow_never_auto_writes"


def decide_candidate(cand: dict[str, Any], *, circuits: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one CANDIDATE row. Never mutates canonical data."""
    reasons: list[str] = []
    decision = SHADOW_ACCEPT
    circuits = circuits or load_circuits()

    if circuits.get("global_open"):
        decision = SHADOW_REJECT
        reasons.append("global_circuit_open")

    if not cand.get("valid_authority"):
        decision = SHADOW_REJECT
        reasons.append("authority_not_official_public_monitor")

    country = (cand.get("source_country") or "").upper()
    if country and country not in {"FR", "EU"}:
        decision = SHADOW_REJECT
        reasons.append(f"source_country_not_fr:{country}")

    if not cand.get("observed"):
        decision = SHADOW_REJECT
        reasons.append("empty_observed")

    streak = int(cand.get("live_high_streak") or cand.get("high_streak") or 0)
    if streak < MIN_LIVE_STREAK:
        if decision == SHADOW_ACCEPT:
            decision = SHADOW_REVIEW
        reasons.append(f"streak_below_{MIN_LIVE_STREAK}:{streak}")

    impact = int(cand.get("announcement_impact") or 0)
    if impact < MIN_IMPACT and decision == SHADOW_ACCEPT:
        decision = SHADOW_REVIEW
        reasons.append("low_impact")

    # Never accept personal identity fields
    if cand.get("field") in {"personal_code", "personal_link"}:
        decision = SHADOW_REJECT
        reasons.append("personal_field_operator_only")

    if _operator_locks_field(cand.get("program"), cand.get("field")):
        decision = SHADOW_REJECT
        reasons.append("operator_validated_or_no_fr_authority")

    allowed, why = may_write_canonical(
        decision, program=cand.get("program"), field=cand.get("field")
    )
    out = {
        "decision": decision,
        "auto_applied": False,
        "would_write": False,
        "reasons": reasons,
        "program": cand.get("program"),
        "field": cand.get("field"),
        "canonical": cand.get("canonical"),
        "observed": cand.get("observed"),
        "source_url": cand.get("source_url"),
        "source_country": cand.get("source_country"),
        "authority": cand.get("authority"),
        "live_high_streak": streak,
        "announcement_impact": impact,
        "observation_only": True,
        "canonical_write_allowed": allowed,
        "canonical_write_block": why,
        "at": _now(),
    }
    return out


def run_shadow(
    results: list[Observation],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Build SHADOW decisions for a monitor run. No live write, no auto-accept."""
    circuits = load_circuits()
    cands = candidates_report(results)
    decisions = [decide_candidate(c, circuits=circuits) for c in cands]

    # Circuit: mass-change guard already flipped CANDIDATE→REVIEW in engine.
    reviews = [r for r in results if r.status == ObservationStatus.REVIEW]
    errors = [r for r in results if r.status == ObservationStatus.ERROR]
    stop_hits: list[dict[str, str]] = []
    for r in errors:
        kind = classify_stop(
            " ".join(r.notes or []) + " " + (r.error or ""),
        )
        if kind:
            stop_hits.append({"program": r.program, "kind": kind})

    by_dec: dict[str, int] = {}
    for d in decisions:
        by_dec[d["decision"]] = by_dec.get(d["decision"], 0) + 1

    report = {
        "mode": "SHADOW",
        "auto_accept": False,
        "auto_write": False,
        "at": _now(),
        "candidates": len(cands),
        "decisions": decisions,
        "by_decision": by_dec,
        "review_observations": len(reviews),
        "error_observations": len(errors),
        "stop_hits": stop_hits,
        "MONITOR_SHADOW_READY": "YES",
        "note": (
            "SHADOW only. Human must explicitly accept before any canonical write. "
            "Never writes offers.json / accepted-fields / platform ads."
        ),
        "production": {
            "MONITORING_PRODUCTION_READY": (
                production_readiness_report(results).get("MONITORING_PRODUCTION_READY")
            )
        },
    }

    if persist:
        SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        with SHADOW_PATH.open("a", encoding="utf-8") as fh:
            for d in decisions:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        SHADOW_REPORT.parent.mkdir(parents=True, exist_ok=True)
        SHADOW_REPORT.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report
