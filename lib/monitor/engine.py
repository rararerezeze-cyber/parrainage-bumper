"""Monitor engine: fetch → parse → normalize → compare → history (observation-only)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.http_fetch import fetch_result
from lib.monitor.history import append_history, load_last_observations
from lib.monitor.models import (
    BUSINESS_FIELDS,
    Confidence,
    FailureCode,
    FieldChange,
    MonitorProgramStatus,
    Observation,
    ObservationStatus,
    OfferKind,
    SourceClass,
    SourceConfig,
)
from lib.monitor.normalize import normalize_field
from lib.monitor.parsers import get_parser
from lib.monitor.registry import coverage_stats, load_registry, mapping_impact_counts
from lib.offers import OffersRepository
from lib.paths import DATA_DIR

HISTORY_DIR = DATA_DIR / "monitor"
LAST_OBS_PATH = HISTORY_DIR / "last-observations.json"
REPORT_PATH = DATA_DIR / "captures" / "monitor-last-report.json"
HIGH_STREAK_FOR_VERIFIED = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_business(offer: dict) -> dict[str, str | None]:
    return {
        "referee_reward": normalize_field("referee_reward", offer.get("reward")),
        "conditions": normalize_field("conditions", offer.get("cond")),
    }


def classify_fetch_failure(status: int, error: str | None, body: str = "") -> FailureCode:
    msg = (error or "").lower()
    low = (body or "")[:2000].lower()
    if status == 404 or "404" in msg:
        return FailureCode.DEAD_URL
    if status == 403:
        return FailureCode.ANTIBOT_403
    if status == 429:
        return FailureCode.RATE_LIMIT
    if status in {401, 407}:
        return FailureCode.APP_ONLY
    if any(x in low or x in msg for x in ("captcha", "challenge", "cf-browser", "just a moment")):
        return FailureCode.CHALLENGE
    if status == 0 or "url error" in msg or "timed out" in msg or "timeout" in msg:
        return FailureCode.TEMPORARY_ERROR
    if status >= 500:
        return FailureCode.TEMPORARY_ERROR
    return FailureCode.FETCH_FAILED


def compare_business(
    program: str,
    canonical: dict[str, str | None],
    observed: dict[str, str | None],
    confidence: Confidence,
) -> tuple[ObservationStatus, list[FieldChange], list[str]]:
    notes: list[str] = []
    changes: list[FieldChange] = []

    if confidence == Confidence.REJECT:
        return ObservationStatus.REJECTED, [], ["rejected_extraction"]

    for k, new_v in list(observed.items()):
        old_v = canonical.get(k)
        if old_v and (new_v is None or str(new_v).strip() == ""):
            notes.append(f"refuse_empty_overwrite:{k}")
            observed[k] = old_v

    for field in BUSINESS_FIELDS:
        if field not in observed and field not in canonical:
            continue
        old = canonical.get(field)
        new = observed.get(field)
        if old is None and new is None:
            continue
        if old is None and new:
            changes.append(FieldChange(field=field, old=None, new=new))
            continue
        if old and new is None:
            notes.append(f"field_missing_in_observation:{field}")
            continue
        if normalize_field(field, old) != normalize_field(field, new):
            changes.append(FieldChange(field=field, old=old, new=new))

    if not changes:
        return ObservationStatus.NO_CHANGE, [], notes

    if confidence == Confidence.HIGH:
        return ObservationStatus.CANDIDATE, changes, notes + ["high_confidence_candidate"]
    if confidence == Confidence.REVIEW:
        return ObservationStatus.REVIEW, changes, notes + ["needs_human_review"]
    return ObservationStatus.REJECTED, changes, notes


def _derive_monitor_status(
    cfg: SourceConfig | None,
    confidence: Confidence,
    failure: FailureCode,
    high_streak: int,
    status: ObservationStatus,
) -> str:
    if cfg is None:
        return MonitorProgramStatus.UNCONFIGURED.value
    kind = cfg.offer_kind
    if kind == OfferKind.APP_PERSONALIZED.value or failure == FailureCode.APP_ONLY:
        return MonitorProgramStatus.APP_PERSONALIZED.value
    if kind == OfferKind.OPERATOR_ONLY.value:
        return MonitorProgramStatus.OPERATOR_ONLY.value
    if cfg.source_class == SourceClass.ANTI_BOT_BLOCKED.value or failure in {
        FailureCode.ANTIBOT_403,
        FailureCode.CHALLENGE,
    }:
        if confidence != Confidence.HIGH:
            return MonitorProgramStatus.ANTI_BOT_BLOCKED.value
    if cfg.source_class in {
        SourceClass.WRONG_OR_DEAD_URL.value,
        SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value,
    }:
        return MonitorProgramStatus.BROKEN.value
    if failure in {FailureCode.DEAD_URL, FailureCode.WRONG_OR_DEAD_URL} and confidence == Confidence.REJECT:
        return MonitorProgramStatus.BROKEN.value
    if high_streak >= HIGH_STREAK_FOR_VERIFIED and confidence == Confidence.HIGH:
        return MonitorProgramStatus.MONITOR_VERIFIED.value
    if status == ObservationStatus.ERROR and failure == FailureCode.TEMPORARY_ERROR:
        return MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value
    return MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value


def _update_high_streak(prev: dict, confidence: Confidence, business_fp: str) -> int:
    if confidence != Confidence.HIGH or not business_fp:
        return 0
    prev_fp = prev.get("business_fingerprint") or ""
    prev_streak = int(prev.get("high_streak") or 0)
    if prev_fp and prev_fp != business_fp:
        # contradictory change between HIGH runs — reset streak
        return 1
    return prev_streak + 1


class MonitorEngine:
    def __init__(self, *, live_fetch: bool = True):
        self.live_fetch = live_fetch
        self.registry = load_registry()
        self.offers = OffersRepository()
        self.last = load_last_observations()
        self.impact = mapping_impact_counts()

    def run_program(self, program: str, *, html_override: str | None = None) -> Observation:
        impact = self.impact.get(program, 0)
        try:
            offer = self.offers.get_by_slug(program)
        except KeyError:
            return Observation(
                program=program,
                status=ObservationStatus.ERROR,
                confidence=Confidence.REJECT,
                source_url=None,
                parser="",
                detected_at=_now(),
                canonical_fields={},
                observed_fields={},
                error="program_not_in_offers",
                failure_code=FailureCode.FETCH_FAILED,
                monitor_status=MonitorProgramStatus.UNCONFIGURED.value,
                impact_count=impact,
            )

        cfg = self.registry.get(program)
        if not cfg or not cfg.enabled:
            return Observation(
                program=program,
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=cfg.source_url if cfg else None,
                parser=(cfg.parser if cfg else ""),
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                notes=["no_source_configured_or_disabled"],
                failure_code=FailureCode.NO_PUBLIC_OFFER,
                source_class=cfg.source_class if cfg else SourceClass.UNVERIFIED.value,
                offer_kind=cfg.offer_kind if cfg else OfferKind.OPERATOR_ONLY.value,
                monitor_status=_derive_monitor_status(
                    cfg, Confidence.REJECT, FailureCode.NO_PUBLIC_OFFER, 0, ObservationStatus.SKIPPED
                ),
                impact_count=impact,
            )

        # Pre-classified non-automatable kinds
        if cfg.offer_kind == OfferKind.OPERATOR_ONLY.value or cfg.source_type in {"manual", "unmonitorable"}:
            return Observation(
                program=program,
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=cfg.source_url,
                parser=cfg.parser,
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                notes=[cfg.notes or "operator_only_or_unmonitorable"],
                failure_code=FailureCode.NO_PUBLIC_OFFER,
                source_class=cfg.source_class,
                offer_kind=cfg.offer_kind,
                monitor_status=MonitorProgramStatus.OPERATOR_ONLY.value,
                impact_count=impact,
            )

        if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value and cfg.parser == "app_personalized_stub":
            # Still may fetch page to confirm program exists, but never HIGH on amount
            pass

        if cfg.auth_required and not cfg.source_url:
            return Observation(
                program=program,
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=None,
                parser=cfg.parser,
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                notes=["auth_required"],
                failure_code=FailureCode.APP_ONLY,
                source_class=cfg.source_class,
                offer_kind=cfg.offer_kind,
                monitor_status=MonitorProgramStatus.APP_PERSONALIZED.value,
                impact_count=impact,
            )

        html = ""
        fetch_status = 200
        failure = FailureCode.NONE
        if html_override is not None:
            html = html_override
        elif not self.live_fetch:
            html = ""
        elif not cfg.source_url:
            failure = FailureCode.NO_PUBLIC_OFFER
            return Observation(
                program=program,
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=None,
                parser=cfg.parser,
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                notes=["no_source_url"],
                failure_code=failure,
                source_class=cfg.source_class,
                offer_kind=cfg.offer_kind,
                monitor_status=_derive_monitor_status(
                    cfg, Confidence.REJECT, failure, 0, ObservationStatus.SKIPPED
                ),
                impact_count=impact,
            )
        else:
            res = fetch_result(cfg.source_url, timeout=25)
            fetch_status = res.status
            if not res.ok:
                failure = classify_fetch_failure(res.status, res.error, res.body)
                return Observation(
                    program=program,
                    status=ObservationStatus.ERROR,
                    confidence=Confidence.REJECT,
                    source_url=cfg.source_url,
                    parser=cfg.parser,
                    detected_at=_now(),
                    canonical_fields=_canonical_business(offer),
                    observed_fields={},
                    error=res.error,
                    notes=["fetch_failed", f"http_status={fetch_status}", failure.value],
                    failure_code=failure,
                    source_class=cfg.source_class,
                    offer_kind=cfg.offer_kind,
                    monitor_status=_derive_monitor_status(
                        cfg, Confidence.REJECT, failure, 0, ObservationStatus.ERROR
                    ),
                    impact_count=impact,
                )
            html = res.body

        parser = get_parser(cfg.parser)
        normalized = parser(html, cfg, offer)
        failure = normalized.failure_code or FailureCode.NONE

        # Field authority: monitor only keeps fields it may write
        observed_raw = {
            k: normalize_field(k, v)
            for k, v in (normalized.fields or {}).items()
            if k in BUSINESS_FIELDS
        }
        observed = {
            k: v
            for k, v in observed_raw.items()
            if cfg.monitor_may_write_field(k)
        }
        dropped = [k for k in observed_raw if k not in observed]
        notes = list(normalized.notes)
        if dropped:
            notes.append(f"field_authority_dropped={dropped}")

        # App-personalized: never promote to HIGH on reward
        if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value and normalized.confidence == Confidence.HIGH:
            if observed.get("referee_reward"):
                normalized.confidence = Confidence.REVIEW
                notes.append("app_personalized_downgrade_high")
                failure = FailureCode.APP_ONLY

        canonical = _canonical_business(offer)
        status, changes, extra_notes = compare_business(
            program, canonical, observed, normalized.confidence
        )
        notes = notes + extra_notes

        business_fp = normalized.business_fingerprint() if observed else ""
        # recompute fingerprint from authority-filtered fields
        if observed:
            from lib.monitor.models import NormalizedOffer as NO

            tmp = NO(program=program, fields=observed)
            business_fp = tmp.business_fingerprint()

        prev = self.last.get(program) or {}
        high_streak = _update_high_streak(prev, normalized.confidence, business_fp)

        html_hint = False
        prev_fp = prev.get("raw_fingerprint") or prev.get("page_fingerprint")
        page_fp = normalized.raw_fingerprint
        if prev_fp and page_fp and prev_fp != page_fp and status == ObservationStatus.NO_CHANGE:
            html_hint = True
            notes.append("html_changed_but_business_unchanged")

        notes.append(f"page_fp={page_fp}")
        notes.append(f"http_status={fetch_status}")

        monitor_status = _derive_monitor_status(
            cfg, normalized.confidence, failure, high_streak, status
        )

        return Observation(
            program=program,
            status=status,
            confidence=normalized.confidence,
            source_url=cfg.source_url,
            parser=cfg.parser,
            detected_at=_now(),
            canonical_fields=canonical,
            observed_fields=observed,
            changes=changes,
            notes=notes,
            business_fingerprint=business_fp,
            html_changed_hint=html_hint,
            failure_code=failure,
            source_class=cfg.source_class,
            offer_kind=cfg.offer_kind,
            monitor_status=monitor_status,
            high_streak=high_streak,
            impact_count=impact,
        )

    def run_all(self) -> list[Observation]:
        programs = [o.get("lk") for o in self.offers.load_all() if o.get("lk")]
        # prioritize high impact first
        programs.sort(key=lambda p: (-self.impact.get(p, 0), p or ""))
        results = [self.run_program(p) for p in programs]
        candidates = [
            r
            for r in results
            if r.status in {ObservationStatus.CANDIDATE, ObservationStatus.REVIEW} and r.changes
        ]
        if len(candidates) >= max(5, len(programs) // 3):
            for r in candidates:
                r.status = ObservationStatus.REVIEW
                r.notes.append("mass_change_guard_triggered")
        return results


def run_program(program: str, **kwargs) -> Observation:
    return MonitorEngine(**kwargs).run_program(program)


def run_all(**kwargs) -> list[Observation]:
    return MonitorEngine(**kwargs).run_all()


def impact_report(observation: Observation) -> dict[str, Any]:
    """Integrate with announcement engine: candidate canonical → platform diffs (dry)."""
    from lib.inventory import list_mapping_refs
    from lib.renderer import MappingRepository, Renderer, TemplateRepository
    from lib.offers import OffersRepository

    if observation.status not in {ObservationStatus.CANDIDATE, ObservationStatus.REVIEW}:
        return {"program": observation.program, "platforms": {}, "note": "no_candidate"}

    offers = OffersRepository()
    try:
        base = dict(offers.get_by_slug(observation.program))
    except KeyError:
        return {"program": observation.program, "platforms": {}, "error": "missing_offer"}

    patch = {}
    for ch in observation.changes:
        if ch.field == "referee_reward":
            patch["reward"] = ch.new
        elif ch.field == "conditions":
            patch["cond"] = ch.new
    offer = {**base, **patch}

    impacts = {}
    renderer = Renderer(offers)
    templates = TemplateRepository()
    repo = MappingRepository()
    for ref in list_mapping_refs():
        if ref.program != observation.program:
            continue
        try:
            mapping = repo.load(ref.platform, ref.program, ref.language)
            template = templates.load_text(ref.platform, ref.program, ref.language)
            rendered_old = renderer.render(template, mapping, offer=base)
            rendered_new = renderer.render(template, mapping, offer=offer)
            impacts[ref.platform] = {
                "would_change": rendered_old != rendered_new,
                "status": "PENDING_UPDATE" if rendered_old != rendered_new else "IN_SYNC",
            }
        except Exception as exc:  # noqa: BLE001
            impacts[ref.platform] = {"error": str(exc), "status": "ERROR"}
    return {
        "program": observation.program,
        "candidate_patch": patch,
        "platforms": impacts,
        "observation_only": True,
    }


def business_signature(results: list[Observation]) -> str:
    """Fingerprint of business-relevant state (excludes detected_at)."""
    parts = []
    for r in sorted(results, key=lambda x: x.program):
        parts.append(
            "|".join(
                [
                    r.program,
                    r.status.value,
                    r.confidence.value,
                    r.business_fingerprint or "",
                    r.failure_code.value,
                    r.monitor_status,
                    str(r.high_streak),
                    ",".join(f"{c.field}:{c.old}->{c.new}" for c in r.changes),
                ]
            )
        )
    return "\n".join(parts)


def has_business_change(results: list[Observation], previous_report: dict | None) -> bool:
    """True if monitor should commit (status/fields/failure changed)."""
    if not previous_report:
        return True
    prev_obs = previous_report.get("observations") or []
    prev_map = {o.get("program"): o for o in prev_obs if o.get("program")}
    for r in results:
        p = prev_map.get(r.program)
        if not p:
            return True
        if p.get("status") != r.status.value:
            return True
        if p.get("confidence") != r.confidence.value:
            return True
        if (p.get("business_fingerprint") or "") != (r.business_fingerprint or ""):
            return True
        if (p.get("failure_code") or "NONE") != r.failure_code.value:
            return True
        if (p.get("monitor_status") or "") != r.monitor_status:
            return True
        prev_changes = p.get("changes") or []
        cur_changes = [c.__dict__ if hasattr(c, "__dict__") else c for c in r.changes]
        # compare field names + values only
        def ch_key(ch):
            if isinstance(ch, dict):
                return (ch.get("field"), ch.get("old"), ch.get("new"))
            return (ch.field, ch.old, ch.new)

        if [ch_key(c) for c in prev_changes] != [ch_key(c) for c in cur_changes]:
            return True
    return False


def production_readiness_report(results: list[Observation], registry: dict[str, SourceConfig] | None = None) -> dict[str, Any]:
    reg = registry or load_registry()
    by_ms: dict[str, int] = {}
    by_fail: dict[str, int] = {}
    by_class: dict[str, int] = {}
    fetch_ok = 0
    stable_high = 0
    structured = 0
    html_det = 0
    for r in results:
        by_ms[r.monitor_status] = by_ms.get(r.monitor_status, 0) + 1
        by_fail[r.failure_code.value] = by_fail.get(r.failure_code.value, 0) + 1
        by_class[r.source_class] = by_class.get(r.source_class, 0) + 1
        if r.status not in {ObservationStatus.ERROR, ObservationStatus.SKIPPED}:
            fetch_ok += 1
        if r.confidence == Confidence.HIGH:
            stable_high += 1
        if any("structured" in n for n in r.notes):
            structured += 1
        if r.parser and "generic" not in r.parser and r.parser not in {"structured_first", "static_canonical_hint"}:
            html_det += 1
        elif r.confidence == Confidence.HIGH and r.parser:
            html_det += 1

    verified = by_ms.get(MonitorProgramStatus.MONITOR_VERIFIED.value, 0)
    impact = mapping_impact_counts()
    verified_programs = {r.program for r in results if r.monitor_status == MonitorProgramStatus.MONITOR_VERIFIED.value}
    mappings_verified = sum(impact.get(p, 0) for p in verified_programs)
    mappings_total = sum(impact.values()) or 149

    # Explicit non-automatable counts
    app_p = by_ms.get(MonitorProgramStatus.APP_PERSONALIZED.value, 0)
    op_only = by_ms.get(MonitorProgramStatus.OPERATOR_ONLY.value, 0)
    antibot = by_ms.get(MonitorProgramStatus.ANTI_BOT_BLOCKED.value, 0)
    broken = by_ms.get(MonitorProgramStatus.BROKEN.value, 0)
    pending = by_ms.get(MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value, 0)

    classified = verified + app_p + op_only + antibot + broken + pending
    # PRODUCTION_READY: every program classified, no UNCONFIGURED majority,
    # and either verified>0 with tests, or remaining are explicit non-automatable.
    # Conservative: YES only if verified + explicit-non-auto covers all AND pending is low
    explicit_done = verified + app_p + op_only + antibot + broken
    production_ready = (
        len(results) >= 30
        and pending <= max(8, len(results) // 3)
        and explicit_done + pending == len(results)
        and broken < len(results) // 2
    )
    # Stricter practical gate: prefer real quality signal
    if verified < 3 and pending > 15:
        production_ready = False

    review = sum(1 for r in results if r.status == ObservationStatus.REVIEW)
    reject = sum(
        1
        for r in results
        if r.status in {ObservationStatus.REJECTED, ObservationStatus.ERROR}
    )

    return {
        "mode": "OBSERVATION_ONLY",
        "programs": len(results),
        "MONITOR_VERIFIED": verified,
        "PUBLIC_MONITORABLE_PENDING": pending,
        "APP_PERSONALIZED": app_p,
        "OPERATOR_ONLY": op_only,
        "ANTI_BOT_BLOCKED": antibot,
        "BROKEN": broken,
        "fetch_success": f"{fetch_ok}/{len(results)}",
        "fetch_ok_count": fetch_ok,
        "stable_high": stable_high,
        "REVIEW": review,
        "REJECT_ERROR": reject,
        "verified_programs_coverage": f"{verified}/{len(results)}",
        "mappings_impacted_by_verified": f"{mappings_verified}/{mappings_total}",
        "mappings_verified_count": mappings_verified,
        "mappings_total": mappings_total,
        "by_monitor_status": by_ms,
        "by_failure_code": by_fail,
        "by_source_class": by_class,
        "structured_or_api_signals": structured,
        "program_specific_or_deterministic": html_det,
        "MONITORING_BASE_READY": "YES",
        "MONITORING_PRODUCTION_READY": "YES" if production_ready else "NO",
        "registry_stats": coverage_stats(reg, len(results)),
    }


def save_run_report(
    results: list[Observation],
    path: Path | None = None,
    *,
    force_history: bool = False,
) -> Path:
    p = path or REPORT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    prev = None
    if p.exists():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            prev = None

    biz_change = has_business_change(results, prev)
    prod = production_readiness_report(results)

    payload = {
        "generated_at": _now(),
        "mode": "OBSERVATION_ONLY",
        "count": len(results),
        "business_change": biz_change,
        "should_commit": biz_change,
        "by_status": {},
        "by_confidence": {},
        "by_failure_code": {},
        "by_monitor_status": {},
        "production": prod,
        "observations": [r.to_dict() for r in results],
        "priority_order": [
            {"program": r.program, "impact_count": r.impact_count, "monitor_status": r.monitor_status}
            for r in sorted(results, key=lambda x: (-x.impact_count, x.program))
        ],
    }
    for r in results:
        payload["by_status"][r.status.value] = payload["by_status"].get(r.status.value, 0) + 1
        payload["by_confidence"][r.confidence.value] = (
            payload["by_confidence"].get(r.confidence.value, 0) + 1
        )
        payload["by_failure_code"][r.failure_code.value] = (
            payload["by_failure_code"].get(r.failure_code.value, 0) + 1
        )
        payload["by_monitor_status"][r.monitor_status] = (
            payload["by_monitor_status"].get(r.monitor_status, 0) + 1
        )
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    last = load_last_observations()
    for r in results:
        last[r.program] = {
            "status": r.status.value,
            "confidence": r.confidence.value,
            "business_fingerprint": r.business_fingerprint,
            "detected_at": r.detected_at,
            "raw_fingerprint": next(
                (n.split("=", 1)[1] for n in r.notes if n.startswith("page_fp=")),
                None,
            ),
            "observed_fields": r.observed_fields,
            "failure_code": r.failure_code.value,
            "monitor_status": r.monitor_status,
            "high_streak": r.high_streak,
            "source_class": r.source_class,
            "offer_kind": r.offer_kind,
            "impact_count": r.impact_count,
        }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LAST_OBS_PATH.write_text(json.dumps(last, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # History: only on business changes (or force)
    if biz_change or force_history:
        for r in results:
            if r.changes or r.status in {
                ObservationStatus.CANDIDATE,
                ObservationStatus.REVIEW,
                ObservationStatus.REJECTED,
                ObservationStatus.ERROR,
            }:
                for ch in r.changes or [FieldChange(field="*", old=None, new=None)]:
                    append_history(
                        {
                            "program": r.program,
                            "field": ch.field,
                            "old": ch.old,
                            "new": ch.new,
                            "detected_at": r.detected_at,
                            "source": r.source_url,
                            "confidence": r.confidence.value,
                            "status": r.status.value,
                            "failure_code": r.failure_code.value,
                            "monitor_status": r.monitor_status,
                            "high_streak": r.high_streak,
                        }
                    )
    return p
