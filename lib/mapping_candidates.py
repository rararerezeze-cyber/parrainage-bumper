"""Candidate observations for curated mapping fields (stages C/D/E).

merge_conservative_mapping_update() (lib/template_builder.py) never lets a
read-only capture silently overwrite a curated, protected mapping field
(stage A). But a genuine future change on the live site must not vanish
just because "existing wins" -- it has to stay visible somewhere until a
human deliberately reviews and promotes it (stages C/D/E):

  A. curated mapping           -- data/platform-mappings/*.json (protected)
  B. new observation           -- what a capture run actually saw
  C. divergence detected       -- record_candidate_divergence() (this file)
  D. validation                -- list_pending_candidates() (a human reads it)
  E. voluntary promotion       -- promote_candidate() (this file)

This module never touches the curated mapping except inside
promote_candidate(), which is never called automatically by a capture run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import MAPPING_CANDIDATES_PATH, mapping_path, sync_entry_key

STATUS_PENDING = "pending"
STATUS_PROMOTED = "promoted"
STATUS_DISMISSED = "dismissed"

# Divergence classification -- an operator's Telegram queue must default to
# real offer content, never the capture engine's own bookkeeping. Real
# incident this guards against: the first real capture produced 116 pending
# candidates for one platform pass, and only a handful were an actual
# code/lien/gain change -- the rest were parser confidence scores, an
# internal `quality`/`sync_mode` label, a freeform note restating something
# already known, or a URL fragment variant of the same announcement. Mixing
# them into one undifferentiated queue would train the operator to ignore
# it.
CLASS_BUSINESS = "BUSINESS"
CLASS_TARGETING = "TARGETING"
CLASS_ENGINE_METADATA = "ENGINE_METADATA"

# A. Real, human-visible offer content -- the only class actionable by
# default. platform_values.* (personal_code, personal_link, referee_reward,
# referrer_reward, conditions, ...) and their top-level equivalents.
_BUSINESS_FIELDS = frozenset(
    {
        "personal_code",
        "personal_link",
        "referee_reward",
        "referrer_reward",
        "conditions",
        "title",
    }
)
# B. Information used to locate/target the right listing, not the offer
# content itself. Visible only in a diagnostic/technical view.
_TARGETING_FIELDS = frozenset(
    {
        "edit_url",
        "platform_offer_id",
        "occurrences",
        "occurrence_count",
        "edit_url_source",
        "edit_url_learned_at",
        "announcement_url",
    }
)
# C. The capture engine's own bookkeeping -- must never look like "your
# offer changed" to a human operator.
_ENGINE_METADATA_FIELDS = frozenset({"sync_mode", "quality", "notes"})


def classify_field(field: str) -> str:
    """A. BUSINESS / B. TARGETING / C. ENGINE_METADATA for a divergence
    field name (including dotted nested keys like "platform_values.title" or
    "confidences.personal_code"). Unknown fields default to TARGETING: never
    silently hidden as noise, but also never presented as a proven offer
    change until someone classifies it explicitly.
    """
    # Nested prefix decides first: confidences.personal_code is a parser
    # confidence score, not the personal_code value itself, even though its
    # leaf name matches a BUSINESS field.
    if "." in field:
        prefix = field.split(".", 1)[0]
        if prefix == "platform_values":
            return CLASS_BUSINESS
        if prefix == "confidences":
            return CLASS_ENGINE_METADATA
        return CLASS_TARGETING
    if field in _BUSINESS_FIELDS:
        return CLASS_BUSINESS
    if field in _ENGINE_METADATA_FIELDS:
        return CLASS_ENGINE_METADATA
    return CLASS_TARGETING


def is_actionable(candidate_class: str) -> bool:
    """Only BUSINESS divergences default into the operator-facing queue."""
    return candidate_class == CLASS_BUSINESS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_for_comparison(field: str, value: Any) -> Any:
    """Best-effort equivalence check so a truly-equivalent value never
    creates divergence noise. NEVER applied to BUSINESS fields -- a real
    code/lien/gain change must never be silently swallowed by a
    normalization rule, whatever the rule.

    Known rule: announcement_url's `#id=...` fragment identifies which
    offer card on a shared listing page an edit_url was learned from; it is
    not a different offer, so a fragment-only diff is not a business
    change. The rule is intentionally narrow (this exact field) rather than
    "any URL" -- edit_url's fragment (if any) may be meaningful and must
    not be silently stripped.
    """
    if classify_field(field) == CLASS_BUSINESS:
        return value
    if not isinstance(value, str):
        return value
    v = value.strip()
    if field == "announcement_url":
        v = v.split("#", 1)[0]
    return v


class MappingCandidateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or MAPPING_CANDIDATES_PATH

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "updated_at": None, "entries": {}}
        with self.path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def _entry_key(platform: str, program: str, language: str, field: str) -> str:
    return f"{sync_entry_key(platform, program, language)}:{field}"


def record_candidate_divergence(
    platform: str,
    program: str,
    language: str,
    field: str,
    curated_value: Any,
    observed_value: Any,
    *,
    store: MappingCandidateStore | None = None,
) -> dict[str, Any]:
    """Stage C. Never touches the curated mapping. Best-effort: a capture
    run should wrap this in try/except so a candidate-tracking failure can
    never break the (already read-only) capture itself.

    Idempotent: re-observing the same divergence just bumps
    last_observed_at/observation_count. If a *third* value shows up later
    (curated still X, but the live site now showed Y, and now shows Z),
    `observed_value` moves to the latest (Z) so stage D always reviews the
    most recent observation -- but Y is never silently discarded: it is
    appended, with its own first/last-seen timestamps and observation
    count, to `entry["history"]` (append-only, oldest first) before being
    superseded. A human reviewing the candidate can see the full X -> Y ->
    Z timeline, not just the latest snapshot.

    Before recording anything, curated_value/observed_value are compared
    after `_normalize_for_comparison()` -- a value that is truly equivalent
    (e.g. the same announcement_url with/without its #id fragment) never
    becomes a candidate at all. Never applied to BUSINESS fields.
    """
    if _normalize_for_comparison(field, curated_value) == _normalize_for_comparison(
        field, observed_value
    ):
        return {"skipped": True, "reason": "equivalent_after_normalization", "field": field}

    candidate_class = classify_field(field)
    store = store or MappingCandidateStore()
    data = store.load()
    entries = data.setdefault("entries", {})
    key = _entry_key(platform, program, language, field)
    entry = entries.get(key)
    now = _now()
    if entry is None or entry.get("status") != STATUS_PENDING:
        entry = {
            "platform": platform,
            "program": program,
            "language": language,
            "field": field,
            "candidate_class": candidate_class,
            "actionable": is_actionable(candidate_class),
            "curated_value": curated_value,
            "observed_value": observed_value,
            "first_observed_at": now,
            "last_observed_at": now,
            "observation_count": 1,
            "status": STATUS_PENDING,
            "history": [],
        }
    else:
        entry["candidate_class"] = candidate_class
        entry["actionable"] = is_actionable(candidate_class)
        entry["curated_value"] = curated_value
        entry.setdefault("history", [])
        if entry.get("observed_value") == observed_value:
            entry["last_observed_at"] = now
            entry["observation_count"] = int(entry.get("observation_count") or 0) + 1
        else:
            # Y is being superseded by Z -- archive Y's full observation
            # window (append-only) before moving on, so it stays visible
            # to whoever reviews this candidate later.
            entry["history"].append(
                {
                    "observed_value": entry.get("observed_value"),
                    "first_observed_at": entry.get("first_observed_at"),
                    "last_observed_at": entry.get("last_observed_at"),
                    "observation_count": entry.get("observation_count"),
                }
            )
            entry["observed_value"] = observed_value
            entry["first_observed_at"] = now
            entry["last_observed_at"] = now
            entry["observation_count"] = 1
    entries[key] = entry
    store.save(data)
    return entry


def list_pending_candidates(
    platform: str | None = None,
    program: str | None = None,
    *,
    candidate_class: str | None = None,
    store: MappingCandidateStore | None = None,
) -> list[dict[str, Any]]:
    """Stage D input: every pending divergence, all classes -- the full
    diagnostic view. Entries recorded before candidate_class existed are
    classified on the fly via classify_field(), never mutating the stored
    file just to answer a read. Pass candidate_class to filter (e.g. only
    ENGINE_METADATA for a technical view).
    """
    store = store or MappingCandidateStore()
    data = store.load()
    out = []
    for entry in (data.get("entries") or {}).values():
        if entry.get("status") != STATUS_PENDING:
            continue
        if platform and entry.get("platform") != platform:
            continue
        if program and entry.get("program") != program:
            continue
        cls = entry.get("candidate_class") or classify_field(str(entry.get("field") or ""))
        if candidate_class and cls != candidate_class:
            continue
        if "candidate_class" not in entry:
            entry = {**entry, "candidate_class": cls, "actionable": is_actionable(cls)}
        out.append(entry)
    return out


def list_operator_queue(
    platform: str | None = None,
    program: str | None = None,
    *,
    store: MappingCandidateStore | None = None,
) -> list[dict[str, Any]]:
    """The default operator-facing queue: BUSINESS, actionable divergences
    only. ENGINE_METADATA/TARGETING noise never appears here -- use
    list_pending_candidates(candidate_class=...) for the diagnostic view.
    """
    return list_pending_candidates(
        platform, program, candidate_class=CLASS_BUSINESS, store=store
    )


def promote_candidate(
    platform: str,
    program: str,
    language: str,
    field: str,
    *,
    actor: str,
    reason: str = "",
    store: MappingCandidateStore | None = None,
) -> dict[str, Any]:
    """Stage E. Explicit, deliberate, human-triggered. Never called by a
    capture run. Writes the candidate's observed_value into the real
    curated mapping field, then marks the candidate promoted.
    """
    from lib.safety import audit, snapshot_state

    store = store or MappingCandidateStore()
    data = store.load()
    entries = data.setdefault("entries", {})
    key = _entry_key(platform, program, language, field)
    entry = entries.get(key)
    if entry is None:
        return {"ok": False, "error": "no_such_candidate"}
    if entry.get("status") != STATUS_PENDING:
        return {"ok": False, "error": f"not_pending:{entry.get('status')}"}

    m_path = mapping_path(platform, program, language)
    if not m_path.exists():
        return {"ok": False, "error": "mapping_file_missing"}

    snapshot_state(f"promote_candidate:{platform}:{program}:{field}")
    mapping = json.loads(m_path.read_text(encoding="utf-8"))
    mapping[field] = entry["observed_value"]
    m_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    entry["status"] = STATUS_PROMOTED
    entry["promoted_at"] = _now()
    entry["promoted_by"] = actor
    entry["promoted_reason"] = reason
    entries[key] = entry
    store.save(data)
    audit(
        "mapping_candidate_promoted",
        platform=platform,
        program=program,
        field=field,
        actor=actor,
        reason=reason,
    )
    return {"ok": True, "field": field, "new_value": entry["observed_value"]}


def dismiss_candidate(
    platform: str,
    program: str,
    language: str,
    field: str,
    *,
    actor: str,
    reason: str = "",
    store: MappingCandidateStore | None = None,
) -> dict[str, Any]:
    """Explicitly reject a candidate (e.g. a bad extraction) without
    touching the curated mapping."""
    store = store or MappingCandidateStore()
    data = store.load()
    entries = data.setdefault("entries", {})
    key = _entry_key(platform, program, language, field)
    entry = entries.get(key)
    if entry is None:
        return {"ok": False, "error": "no_such_candidate"}
    if entry.get("status") != STATUS_PENDING:
        return {"ok": False, "error": f"not_pending:{entry.get('status')}"}
    entry["status"] = STATUS_DISMISSED
    entry["dismissed_at"] = _now()
    entry["dismissed_by"] = actor
    entry["dismissed_reason"] = reason
    entries[key] = entry
    store.save(data)
    return {"ok": True}
