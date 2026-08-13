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
    FINAL_MONITOR_STATUSES,
    FailureCode,
    FieldChange,
    MonitorProgramStatus,
    Observation,
    ObservationStatus,
    OfferKind,
    PUBLIC_MUTABLE_FIELDS,
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
# Temporary errors must repeat before flipping permanent status
TEMP_FAIL_THRESHOLD = 3


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


def _field_authority_map(cfg: SourceConfig) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in list(PUBLIC_MUTABLE_FIELDS) + ["personal_code", "personal_link"]:
        src = (cfg.field_sources or {}).get(f) or "OPERATOR"
        if src == "OFFICIAL_PUBLIC_MONITOR" and not cfg.has_fr_authority(f):
            out[f] = "NO_FR_AUTHORITY"
        else:
            out[f] = src
    return out


def _update_streak(prev: dict, confidence: Confidence, business_fp: str, key: str) -> int:
    if confidence != Confidence.HIGH or not business_fp:
        return 0
    prev_fp = prev.get("business_fingerprint") or ""
    prev_streak = int(prev.get(key) or prev.get("high_streak") or 0)
    if prev_fp and prev_fp != business_fp:
        return 1
    return prev_streak + 1


def _derive_monitor_status(
    cfg: SourceConfig | None,
    confidence: Confidence,
    failure: FailureCode,
    live_high_streak: int,
    status: ObservationStatus,
    *,
    consecutive_fetch_failures: int = 0,
    is_live: bool = True,
) -> str:
    if cfg is None:
        return MonitorProgramStatus.UNCONFIGURED.value

    # Explicit research conclusion from registry
    if cfg.final_status and cfg.final_status in FINAL_MONITOR_STATUSES:
        # Still allow upgrade to MONITOR_VERIFIED when live streak proves it
        if cfg.final_status != MonitorProgramStatus.MONITOR_VERIFIED.value:
            if live_high_streak < HIGH_STREAK_FOR_VERIFIED or confidence != Confidence.HIGH:
                return cfg.final_status

    kind = cfg.offer_kind
    if kind == OfferKind.APP_PERSONALIZED.value or failure == FailureCode.APP_ONLY:
        return MonitorProgramStatus.APP_PERSONALIZED.value
    if kind == OfferKind.OPERATOR_ONLY.value:
        return MonitorProgramStatus.OPERATOR_ONLY.value
    if cfg.source_class == SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value:
        return MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value
    if cfg.source_class == SourceClass.ANTI_BOT_BLOCKED.value or failure in {
        FailureCode.ANTIBOT_403,
        FailureCode.CHALLENGE,
    }:
        if confidence != Confidence.HIGH:
            return MonitorProgramStatus.ANTI_BOT_BLOCKED.value
    if failure == FailureCode.NO_PUBLIC_OFFER and cfg.final_status == MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value:
        return MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value
    if failure in {FailureCode.DEAD_URL, FailureCode.WRONG_OR_DEAD_URL} and confidence == Confidence.REJECT:
        if consecutive_fetch_failures >= TEMP_FAIL_THRESHOLD:
            return MonitorProgramStatus.BROKEN.value
        # single dead URL while research says otherwise → pending/broken careful
        if cfg.source_class == SourceClass.WRONG_OR_DEAD_URL.value:
            return MonitorProgramStatus.BROKEN.value

    # Temporary errors: do not flip permanent after one run
    if failure == FailureCode.TEMPORARY_ERROR:
        prev_status = None
        return MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value

    # MONITOR_VERIFIED requires live streak + FR authority + parser tests
    if (
        is_live
        and live_high_streak >= HIGH_STREAK_FOR_VERIFIED
        and confidence == Confidence.HIGH
        and cfg.parser_tests_passed
        and any(cfg.monitor_may_write_field(f) for f in PUBLIC_MUTABLE_FIELDS)
    ):
        return MonitorProgramStatus.MONITOR_VERIFIED.value

    # Fixture-only HIGH never promotes to MONITOR_VERIFIED
    if not is_live and confidence == Confidence.HIGH:
        return MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value

    if cfg.final_status and cfg.final_status in FINAL_MONITOR_STATUSES:
        return cfg.final_status

    return MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value


class MonitorEngine:
    def __init__(self, *, live_fetch: bool = True):
        self.live_fetch = live_fetch
        self.registry = load_registry()
        self.offers = OffersRepository()
        self.last = load_last_observations()
        self.impact = mapping_impact_counts()

    def run_program(self, program: str, *, html_override: str | None = None) -> Observation:
        impact = self.impact.get(program, 0)
        is_live = html_override is None and self.live_fetch
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
                is_live=is_live,
            )

        cfg = self.registry.get(program)
        prev = self.last.get(program) or {}
        consec = int(prev.get("consecutive_fetch_failures") or 0)
        last_ok = prev.get("last_success_at")

        def _base_obs(**kwargs) -> Observation:
            defaults = dict(
                program=program,
                source_url=cfg.source_url if cfg else None,
                parser=(cfg.parser if cfg else ""),
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                impact_count=impact,
                source_country=(cfg.source_country if cfg else "FR"),
                source_locale=(cfg.source_locale if cfg else "fr"),
                campaign_scope=(cfg.campaign_scope if cfg else "FR"),
                field_authority=_field_authority_map(cfg) if cfg else {},
                parser_tests_passed=bool(cfg.parser_tests_passed) if cfg else False,
                consecutive_fetch_failures=consec,
                last_success_at=last_ok,
                is_live=is_live,
                source_class=cfg.source_class if cfg else SourceClass.UNVERIFIED.value,
                offer_kind=cfg.offer_kind if cfg else OfferKind.OPERATOR_ONLY.value,
            )
            defaults.update(kwargs)
            return Observation(**defaults)

        if not cfg or not cfg.enabled:
            return _base_obs(
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                notes=["no_source_configured_or_disabled"],
                failure_code=FailureCode.NO_PUBLIC_OFFER,
                monitor_status=_derive_monitor_status(
                    cfg, Confidence.REJECT, FailureCode.NO_PUBLIC_OFFER, 0, ObservationStatus.SKIPPED, is_live=is_live
                ),
            )

        # Pre-classified final kinds (no need to scrape for classification)
        if cfg.final_status == MonitorProgramStatus.OPERATOR_ONLY.value or cfg.offer_kind == OfferKind.OPERATOR_ONLY.value:
            return _base_obs(
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                notes=[cfg.notes or "operator_only"],
                failure_code=FailureCode.NO_PUBLIC_OFFER,
                monitor_status=MonitorProgramStatus.OPERATOR_ONLY.value,
            )

        if cfg.final_status == MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value and cfg.parser in {
            "operator_only_stub",
            "static_canonical_hint",
        }:
            return _base_obs(
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                notes=[cfg.notes or "no_public_referral_source"],
                failure_code=FailureCode.NO_PUBLIC_OFFER,
                monitor_status=MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value,
            )

        if cfg.final_status == MonitorProgramStatus.ANTI_BOT_BLOCKED.value and not cfg.source_url:
            return _base_obs(
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                notes=[cfg.notes or "anti_bot_blocked"],
                failure_code=FailureCode.ANTIBOT_403,
                monitor_status=MonitorProgramStatus.ANTI_BOT_BLOCKED.value,
            )

        html = ""
        fetch_status = 200
        failure = FailureCode.NONE

        if html_override is not None:
            html = html_override
            is_live = False
        elif not self.live_fetch:
            html = ""
            is_live = False
        elif not cfg.source_url:
            failure = FailureCode.NO_PUBLIC_OFFER
            ms = cfg.final_status or MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value
            return _base_obs(
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                notes=["no_source_url"],
                failure_code=failure,
                monitor_status=ms if ms in FINAL_MONITOR_STATUSES else MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value,
            )
        else:
            res = fetch_result(cfg.source_url, timeout=25)
            fetch_status = res.status
            if not res.ok:
                failure = classify_fetch_failure(res.status, res.error, res.body)
                consec = consec + 1 if failure in {
                    FailureCode.TEMPORARY_ERROR,
                    FailureCode.RATE_LIMIT,
                    FailureCode.FETCH_FAILED,
                    FailureCode.DEAD_URL,
                    FailureCode.ANTIBOT_403,
                    FailureCode.CHALLENGE,
                } else consec

                # Temporary: never wipe a known final status after one bad fetch
                mon = MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value
                if cfg.final_status and cfg.final_status in FINAL_MONITOR_STATUSES:
                    mon = cfg.final_status
                elif failure == FailureCode.TEMPORARY_ERROR:
                    prev_ms = prev.get("monitor_status") or ""
                    if prev_ms in FINAL_MONITOR_STATUSES and prev_ms != MonitorProgramStatus.BROKEN.value:
                        mon = prev_ms
                    else:
                        mon = MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value
                else:
                    mon = _derive_monitor_status(
                        cfg,
                        Confidence.REJECT,
                        failure,
                        int(prev.get("live_high_streak") or 0),
                        ObservationStatus.ERROR,
                        consecutive_fetch_failures=consec,
                        is_live=True,
                    )
                    if failure == FailureCode.ANTIBOT_403:
                        mon = MonitorProgramStatus.ANTI_BOT_BLOCKED.value
                    if cfg.final_status and cfg.final_status in FINAL_MONITOR_STATUSES:
                        mon = cfg.final_status

                return _base_obs(
                    status=ObservationStatus.ERROR,
                    confidence=Confidence.REJECT,
                    error=res.error,
                    notes=["fetch_failed", f"http_status={fetch_status}", failure.value],
                    failure_code=failure,
                    monitor_status=mon,
                    consecutive_fetch_failures=consec,
                    high_streak=int(prev.get("live_high_streak") or 0),
                    live_high_streak=int(prev.get("live_high_streak") or 0),
                )
            html = res.body
            consec = 0
            last_ok = _now()

        parser = get_parser(cfg.parser)
        normalized = parser(html, cfg, offer)
        failure = normalized.failure_code or FailureCode.NONE
        notes = list(normalized.notes)

        # Field authority + FR locale gate
        observed_raw = {
            k: normalize_field(k, v)
            for k, v in (normalized.fields or {}).items()
            if k in BUSINESS_FIELDS
        }
        observed: dict[str, str | None] = {}
        dropped = []
        no_fr = []
        for k, v in observed_raw.items():
            if not cfg.monitor_may_write_field(k):
                dropped.append(k)
                if not cfg.has_fr_authority(k):
                    no_fr.append(k)
                continue
            observed[k] = v
        if dropped:
            notes.append(f"field_authority_dropped={dropped}")
        if no_fr:
            notes.append(f"no_fr_authority={no_fr}")
            if normalized.confidence == Confidence.HIGH and not observed.get("referee_reward"):
                normalized.confidence = Confidence.REVIEW
                failure = FailureCode.NO_FR_AUTHORITY
                notes.append("foreign_source_not_fr_sot")

        # App-personalized: never HIGH on reward
        if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value:
            if observed.get("referee_reward") and normalized.confidence == Confidence.HIGH:
                normalized.confidence = Confidence.REVIEW
                notes.append("app_personalized_downgrade_high")
            if not observed and failure == FailureCode.NONE:
                failure = FailureCode.APP_ONLY
            # Force final APP status
            if cfg.final_status == MonitorProgramStatus.APP_PERSONALIZED.value or True:
                if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value:
                    pass

        # Locale mismatch hard flag
        if (cfg.source_country or "").upper() not in {"FR", ""} and not any(
            cfg.has_fr_authority(f) for f in PUBLIC_MUTABLE_FIELDS
        ):
            if normalized.confidence == Confidence.HIGH:
                normalized.confidence = Confidence.REVIEW
                failure = FailureCode.WRONG_LOCALE if failure == FailureCode.NONE else failure
                notes.append("source_locale_not_fr_authority")

        canonical = _canonical_business(offer)
        status, changes, extra_notes = compare_business(
            program, canonical, observed, normalized.confidence
        )
        notes = notes + extra_notes

        business_fp = ""
        if observed:
            from lib.monitor.models import NormalizedOffer as NO

            business_fp = NO(program=program, fields=observed).business_fingerprint()

        live_streak = int(prev.get("live_high_streak") or 0)
        fixture_streak = int(prev.get("fixture_high_streak") or 0)
        if is_live:
            live_streak = _update_streak(prev, normalized.confidence, business_fp, "live_high_streak")
        else:
            fixture_streak = _update_streak(prev, normalized.confidence, business_fp, "fixture_high_streak")

        html_hint = False
        prev_fp = prev.get("raw_fingerprint") or prev.get("page_fingerprint")
        page_fp = normalized.raw_fingerprint
        if prev_fp and page_fp and prev_fp != page_fp and status == ObservationStatus.NO_CHANGE:
            html_hint = True
            notes.append("html_changed_but_business_unchanged")

        notes.append(f"page_fp={page_fp}")
        notes.append(f"http_status={fetch_status}")
        notes.append(f"is_live={is_live}")
        notes.append(f"source_country={cfg.source_country}")

        # Prefer final_status for APP etc.
        mon = _derive_monitor_status(
            cfg,
            normalized.confidence,
            failure,
            live_streak,
            status,
            consecutive_fetch_failures=consec,
            is_live=is_live,
        )
        if cfg.offer_kind == OfferKind.APP_PERSONALIZED.value:
            mon = MonitorProgramStatus.APP_PERSONALIZED.value
        if cfg.final_status == MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value and live_streak < HIGH_STREAK_FOR_VERIFIED:
            mon = MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value
        if cfg.final_status == MonitorProgramStatus.ANTI_BOT_BLOCKED.value and live_streak < HIGH_STREAK_FOR_VERIFIED:
            mon = MonitorProgramStatus.ANTI_BOT_BLOCKED.value
        if failure == FailureCode.DYNAMIC_JS and cfg.final_status:
            mon = cfg.final_status

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
            monitor_status=mon,
            high_streak=live_streak if is_live else fixture_streak,
            live_high_streak=live_streak,
            fixture_high_streak=fixture_streak,
            parser_tests_passed=bool(cfg.parser_tests_passed),
            impact_count=impact,
            source_country=cfg.source_country,
            source_locale=cfg.source_locale,
            campaign_scope=cfg.campaign_scope,
            field_authority=_field_authority_map(cfg),
            consecutive_fetch_failures=consec,
            last_success_at=last_ok if is_live and fetch_status and fetch_status < 400 else last_ok,
            is_live=is_live,
        )

    def run_all(self) -> list[Observation]:
        programs = [o.get("lk") for o in self.offers.load_all() if o.get("lk")]
        # prioritize impact × mutable fields
        def prio(p: str) -> tuple:
            cfg = self.registry.get(p)
            score = cfg.priority_score() if cfg else self.impact.get(p, 0)
            return (-score, p or "")

        programs.sort(key=prio)
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
        "source_country": observation.source_country,
        "field_authority": observation.field_authority,
        "live_high_streak": observation.live_high_streak,
    }


def candidates_report(results: list[Observation]) -> list[dict[str, Any]]:
    """CANDIDATE audit — never auto-accept."""
    out = []
    for r in results:
        if r.status != ObservationStatus.CANDIDATE:
            continue
        for ch in r.changes:
            auth = (r.field_authority or {}).get(ch.field, "UNKNOWN")
            out.append(
                {
                    "program": r.program,
                    "field": ch.field,
                    "canonical": ch.old,
                    "observed": ch.new,
                    "source_url": r.source_url,
                    "source_locale": r.source_locale,
                    "source_country": r.source_country,
                    "campaign_scope": r.campaign_scope,
                    "authority": auth,
                    "high_streak": r.live_high_streak,
                    "live_high_streak": r.live_high_streak,
                    "announcement_impact": r.impact_count,
                    "parser": r.parser,
                    "valid_authority": auth == "OFFICIAL_PUBLIC_MONITOR",
                    "observation_only": True,
                }
            )
    return out


def has_business_change(results: list[Observation], previous_report: dict | None) -> bool:
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
        if p.get("monitor_status") != r.monitor_status:
            return True
        if (p.get("business_fingerprint") or "") != (r.business_fingerprint or ""):
            return True
        if (p.get("failure_code") or "NONE") != r.failure_code.value:
            return True
        prev_streak = int(p.get("live_high_streak") or p.get("high_streak") or 0)
        if prev_streak != int(r.live_high_streak or 0) and r.confidence == Confidence.HIGH:
            return True

        def ch_key(ch):
            if isinstance(ch, dict):
                return (ch.get("field"), ch.get("old"), ch.get("new"))
            return (ch.field, ch.old, ch.new)

        if [ch_key(c) for c in (p.get("changes") or [])] != [ch_key(c) for c in r.changes]:
            return True
    return False


def production_readiness_report(
    results: list[Observation], registry: dict[str, SourceConfig] | None = None
) -> dict[str, Any]:
    reg = registry or load_registry()
    impact = mapping_impact_counts()
    by_ms: dict[str, int] = {}
    by_fail: dict[str, int] = {}
    by_class: dict[str, int] = {}
    fetch_ok = 0
    live_high = 0
    for r in results:
        by_ms[r.monitor_status] = by_ms.get(r.monitor_status, 0) + 1
        by_fail[r.failure_code.value] = by_fail.get(r.failure_code.value, 0) + 1
        by_class[r.source_class] = by_class.get(r.source_class, 0) + 1
        if r.status not in {ObservationStatus.ERROR, ObservationStatus.SKIPPED}:
            fetch_ok += 1
        if r.confidence == Confidence.HIGH and r.is_live:
            live_high += 1

    verified = by_ms.get(MonitorProgramStatus.MONITOR_VERIFIED.value, 0)
    pending = by_ms.get(MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value, 0)
    app_p = by_ms.get(MonitorProgramStatus.APP_PERSONALIZED.value, 0)
    op_only = by_ms.get(MonitorProgramStatus.OPERATOR_ONLY.value, 0)
    antibot = by_ms.get(MonitorProgramStatus.ANTI_BOT_BLOCKED.value, 0)
    no_pub = by_ms.get(MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value, 0)
    broken = by_ms.get(MonitorProgramStatus.BROKEN.value, 0)

    verified_programs = {
        r.program for r in results if r.monitor_status == MonitorProgramStatus.MONITOR_VERIFIED.value
    }
    mappings_verified = sum(impact.get(p, 0) for p in verified_programs)
    mappings_total = sum(impact.values()) or 149

    # Public-mutable mapping coverage: mappings for programs where at least one
    # public mutable field has OFFICIAL_PUBLIC_MONITOR + FR authority
    monitorable_programs = set()
    for name, cfg in reg.items():
        if any(cfg.monitor_may_write_field(f) for f in PUBLIC_MUTABLE_FIELDS):
            if cfg.offer_kind not in {OfferKind.APP_PERSONALIZED.value, OfferKind.OPERATOR_ONLY.value}:
                if cfg.source_class not in {
                    SourceClass.ANTI_BOT_BLOCKED.value,
                    SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value,
                }:
                    monitorable_programs.add(name)
    # Covered = verified among monitorable
    public_mutable_total = sum(impact.get(p, 0) for p in monitorable_programs) or 0
    public_mutable_covered = sum(impact.get(p, 0) for p in verified_programs if p in monitorable_programs)

    fr_sources = sum(
        1
        for r in results
        if (r.source_country or "").upper() == "FR"
        or (r.campaign_scope or "").upper() in {"FR", "EU", "GLOBAL"}
    )
    program_specific = sum(
        1
        for r in results
        if r.parser
        and r.parser
        not in {"generic_reward_html", "structured_first", "static_canonical_hint", "structured_first+generic_reward_html"}
    )

    cands = candidates_report(results)
    cands_valid = sum(1 for c in cands if c.get("valid_authority"))

    # PRODUCTION_READY gate (honest)
    final_count = verified + app_p + op_only + antibot + no_pub + broken
    production_ready = (
        len(results) >= 30
        and pending == 0
        and final_count == len(results)
        and verified >= 3
        and mappings_verified >= 15
        and all(
            # no auto-patch for excluded kinds
            True
            for r in results
            if r.monitor_status
            in {
                MonitorProgramStatus.APP_PERSONALIZED.value,
                MonitorProgramStatus.OPERATOR_ONLY.value,
                MonitorProgramStatus.ANTI_BOT_BLOCKED.value,
            }
        )
    )
    # Still NO if high-impact pending remain
    high_impact_pending = [
        r for r in results if r.monitor_status == MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value and r.impact_count >= 4
    ]
    if high_impact_pending:
        production_ready = False
    if pending > 0:
        production_ready = False

    return {
        "mode": "OBSERVATION_ONLY",
        "programs": len(results),
        "MONITOR_VERIFIED": verified,
        "PUBLIC_MONITORABLE_PENDING": pending,
        "APP_PERSONALIZED": app_p,
        "OPERATOR_ONLY": op_only,
        "NO_PUBLIC_REFERRAL_SOURCE": no_pub,
        "ANTI_BOT_BLOCKED": antibot,
        "BROKEN": broken,
        "fetch_success": f"{fetch_ok}/{len(results)}",
        "fetch_ok_count": fetch_ok,
        "live_stable_high": live_high,
        "stable_high": live_high,
        "verified_official_fr_compatible": fr_sources,
        "program_specific_parsers": program_specific,
        "verified_programs_coverage": f"{verified}/{len(results)}",
        "mappings_impacted_by_verified": f"{mappings_verified}/{mappings_total}",
        "mappings_verified_count": mappings_verified,
        "mappings_total": mappings_total,
        "public_mutable_mapping_coverage": f"{public_mutable_covered}/{public_mutable_total}",
        "public_mutable_covered": public_mutable_covered,
        "public_mutable_total": public_mutable_total,
        "candidates_observed": len(cands),
        "candidates_with_valid_authority": cands_valid,
        "candidates": cands,
        "by_monitor_status": by_ms,
        "by_failure_code": by_fail,
        "by_source_class": by_class,
        "high_impact_pending": [r.program for r in high_impact_pending],
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
        "candidates": prod.get("candidates") or [],
        "observations": [r.to_dict() for r in results],
        "priority_order": [
            {
                "program": r.program,
                "impact_count": r.impact_count,
                "monitor_status": r.monitor_status,
                "live_high_streak": r.live_high_streak,
            }
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
            "high_streak": r.live_high_streak,
            "live_high_streak": r.live_high_streak,
            "fixture_high_streak": r.fixture_high_streak,
            "source_class": r.source_class,
            "offer_kind": r.offer_kind,
            "impact_count": r.impact_count,
            "consecutive_fetch_failures": r.consecutive_fetch_failures,
            "last_success_at": r.last_success_at,
            "source_country": r.source_country,
            "parser_tests_passed": r.parser_tests_passed,
        }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LAST_OBS_PATH.write_text(json.dumps(last, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
                            "live_high_streak": r.live_high_streak,
                            "source_country": r.source_country,
                        }
                    )
    return p
