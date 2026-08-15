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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """
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
            "curated_value": curated_value,
            "observed_value": observed_value,
            "first_observed_at": now,
            "last_observed_at": now,
            "observation_count": 1,
            "status": STATUS_PENDING,
            "history": [],
        }
    else:
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
    store: MappingCandidateStore | None = None,
) -> list[dict[str, Any]]:
    """Stage D input: what an operator should review."""
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
        out.append(entry)
    return out


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
