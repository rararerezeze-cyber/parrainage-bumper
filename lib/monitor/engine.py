"""Monitor engine: fetch → parse → normalize → compare → history (observation-only)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.http_fetch import fetch_text
from lib.monitor.history import append_history, load_last_observations
from lib.monitor.models import (
    BUSINESS_FIELDS,
    Confidence,
    FieldChange,
    Observation,
    ObservationStatus,
    SourceConfig,
)
from lib.monitor.normalize import normalize_field
from lib.monitor.parsers import get_parser
from lib.monitor.registry import coverage_stats, load_registry
from lib.offers import OffersRepository
from lib.paths import DATA_DIR

HISTORY_DIR = DATA_DIR / "monitor"
LAST_OBS_PATH = HISTORY_DIR / "last-observations.json"
REPORT_PATH = DATA_DIR / "captures" / "monitor-last-report.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_business(offer: dict) -> dict[str, str | None]:
    return {
        "referee_reward": normalize_field("referee_reward", offer.get("reward")),
        "conditions": normalize_field("conditions", offer.get("cond")),
        # personal code/link intentionally NOT monitored
    }


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

    # Never wipe existing canonical to empty
    for k, new_v in list(observed.items()):
        old_v = canonical.get(k)
        if old_v and (new_v is None or str(new_v).strip() == ""):
            notes.append(f"refuse_empty_overwrite:{k}")
            observed[k] = old_v  # do not propose empty

    for field in BUSINESS_FIELDS:
        if field not in observed and field not in canonical:
            continue
        old = canonical.get(field)
        new = observed.get(field)
        if old is None and new is None:
            continue
        if old is None and new:
            # new info — candidate only if HIGH
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
        # still CANDIDATE in observation mode (no auto-accept)
        return ObservationStatus.CANDIDATE, changes, notes + ["high_confidence_candidate"]
    if confidence == Confidence.REVIEW:
        return ObservationStatus.REVIEW, changes, notes + ["needs_human_review"]
    return ObservationStatus.REJECTED, changes, notes


class MonitorEngine:
    def __init__(self, *, live_fetch: bool = True):
        self.live_fetch = live_fetch
        self.registry = load_registry()
        self.offers = OffersRepository()
        self.last = load_last_observations()

    def run_program(self, program: str, *, html_override: str | None = None) -> Observation:
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
            )

        cfg = self.registry.get(program)
        if not cfg or not cfg.enabled or not cfg.source_url:
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
            )

        if cfg.source_type in {"manual", "unmonitorable"} or cfg.auth_required:
            return Observation(
                program=program,
                status=ObservationStatus.SKIPPED,
                confidence=Confidence.REJECT,
                source_url=cfg.source_url,
                parser=cfg.parser,
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                notes=[cfg.notes or "manual_or_unmonitorable"],
            )

        try:
            if html_override is not None:
                html = html_override
            elif not self.live_fetch:
                html = ""
            else:
                html = fetch_text(cfg.source_url, timeout=25)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            conf = Confidence.REJECT
            notes = ["fetch_failed"]
            if any(x in msg for x in ("403", "429", "captcha", "challenge")):
                notes.append("rate_limit_or_challenge")
            return Observation(
                program=program,
                status=ObservationStatus.ERROR,
                confidence=conf,
                source_url=cfg.source_url,
                parser=cfg.parser,
                detected_at=_now(),
                canonical_fields=_canonical_business(offer),
                observed_fields={},
                error=str(exc),
                notes=notes,
            )

        parser = get_parser(cfg.parser)
        normalized = parser(html, cfg, offer)
        canonical = _canonical_business(offer)
        # only compare supported business fields present
        observed = {
            k: normalize_field(k, v)
            for k, v in (normalized.fields or {}).items()
            if k in BUSINESS_FIELDS
        }

        status, changes, extra_notes = compare_business(
            program, canonical, observed, normalized.confidence
        )
        notes = list(normalized.notes) + extra_notes

        # Detect HTML change without business change
        html_hint = False
        prev = self.last.get(program) or {}
        prev_fp = prev.get("raw_fingerprint") or prev.get("page_fingerprint")
        page_fp = normalized.raw_fingerprint
        if prev_fp and page_fp and prev_fp != page_fp and status == ObservationStatus.NO_CHANGE:
            html_hint = True
            notes.append("html_changed_but_business_unchanged")

        # Massive simultaneous change protection is applied at batch level
        obs = Observation(
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
            business_fingerprint=normalized.business_fingerprint(),
            html_changed_hint=html_hint,
        )
        # stash page fingerprint into notes meta for history
        obs.notes.append(f"page_fp={page_fp}")
        return obs

    def run_all(self) -> list[Observation]:
        programs = [o.get("lk") for o in self.offers.load_all() if o.get("lk")]
        results = [self.run_program(p) for p in programs]
        # mass-change guard
        candidates = [r for r in results if r.status in {ObservationStatus.CANDIDATE, ObservationStatus.REVIEW} and r.changes]
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

    # Build temporary offer override in memory
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
            golden = templates.load_golden(ref.platform, ref.program, ref.language)
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


def save_run_report(results: list[Observation], path: Path | None = None) -> Path:
    p = path or REPORT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": _now(),
        "mode": "OBSERVATION_ONLY",
        "count": len(results),
        "by_status": {},
        "by_confidence": {},
        "observations": [r.to_dict() for r in results],
    }
    for r in results:
        payload["by_status"][r.status.value] = payload["by_status"].get(r.status.value, 0) + 1
        payload["by_confidence"][r.confidence.value] = (
            payload["by_confidence"].get(r.confidence.value, 0) + 1
        )
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # update last observations light store (no giant HTML)
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
        }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LAST_OBS_PATH.write_text(json.dumps(last, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for r in results:
        if r.changes or r.status in {ObservationStatus.CANDIDATE, ObservationStatus.REVIEW, ObservationStatus.REJECTED}:
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
                    }
                )
    return p
