"""Centralized AutoFresh observability contract.

AutoFresh is the backend; **Hermes owns Telegram**. This module never talks to
Telegram and never creates a second bot. It produces one durable, structured,
secret-free event stream that the existing Hermes plugin can read and relay:

    AutoFresh runtime → lib.notify.emit() → data/notifications/outbox.jsonl
                      → Hermes (local plugin) → Telegram

Design rules (all enforced and tested):

* **Allow-list, not deny-list.** Only events that a human would actually want
  to know about are recorded. Routine NO_CHANGE cycles, polls, and technical
  chatter are dropped by ``should_notify`` -- silently, and by construction.
* **Deduplication is mandatory.** A recurring, already-reported condition
  (typically an expected external blocker firing on every cron tick) is
  emitted at most once per TTL window.
* **BEST_EFFORT / FAIL_OPEN.** ``emit`` never raises and never propagates an
  I/O error. A broken notification path must never fail a bump or a business
  write. Callers may ignore the return value entirely.
* **No secrets.** The payload is a closed field whitelist, every string value
  is scrubbed and length-capped, and any credential-shaped content is
  replaced by ``[REDACTED]``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from lib.paths import DATA_DIR

NOTIFY_DIR = DATA_DIR / "notifications"
OUTBOX_PATH = NOTIFY_DIR / "outbox.jsonl"
DEDUP_PATH = NOTIFY_DIR / "dedup.json"

SCHEMA_VERSION = 1

# -- levels -------------------------------------------------------------------
LEVEL_INFO = "INFO"
LEVEL_SUCCESS = "SUCCESS"
LEVEL_WARNING = "WARNING"
LEVEL_ERROR = "ERROR"
LEVEL_HUMAN_REQUIRED = "HUMAN_REQUIRED"

LEVELS = (
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    LEVEL_ERROR,
    LEVEL_HUMAN_REQUIRED,
)

# -- notifiable events (allow-list) -------------------------------------------
EVENT_REAL_WRITE = "real_write"
EVENT_POST_VERIFY_SUCCESS = "post_verify_success"
EVENT_POST_VERIFY_FAILURE = "post_verify_failure"
EVENT_MONITOR_REAL_SAFE_DIFF = "monitor_real_safe_diff"
EVENT_PLATFORM_STATUS_CHANGE = "platform_status_change"
EVENT_WORKFLOW_ERROR = "workflow_error"
EVENT_HUMAN_REQUIRED = "human_required"
EVENT_ROLLBACK = "rollback"
EVENT_PENDING_CREATED = "pending_created"
EVENT_PENDING_CLOSED = "pending_closed"
EVENT_CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
EVENT_CANARY_REAL = "canary_real"
EVENT_BUMP_NOTABLE = "bump_notable"
EVENT_EXTERNAL_BLOCKER = "external_blocker"

NOTIFIABLE_EVENTS = frozenset(
    {
        EVENT_REAL_WRITE,
        EVENT_POST_VERIFY_SUCCESS,
        EVENT_POST_VERIFY_FAILURE,
        EVENT_MONITOR_REAL_SAFE_DIFF,
        EVENT_PLATFORM_STATUS_CHANGE,
        EVENT_WORKFLOW_ERROR,
        EVENT_HUMAN_REQUIRED,
        EVENT_ROLLBACK,
        EVENT_PENDING_CREATED,
        EVENT_PENDING_CLOSED,
        EVENT_CIRCUIT_BREAKER_OPEN,
        EVENT_CANARY_REAL,
        EVENT_BUMP_NOTABLE,
        EVENT_EXTERNAL_BLOCKER,
    }
)

# Explicit, documented non-events. Kept as a named set so "why is this silent?"
# has an answer in code rather than in a chat log.
NEVER_NOTIFY_EVENTS = frozenset(
    {
        "no_change_cycle",
        "cycle_no_change",
        "poll",
        "poll_tick",
        "heartbeat",
        "debug",
        "trace",
        "capture_readonly",
        "dry_run",
    }
)

# -- deduplication -------------------------------------------------------------
DEFAULT_DEDUP_TTL_SECONDS = 6 * 3600
EVENT_DEDUP_TTL_SECONDS = {
    # A cron-driven external gate would otherwise report on every single tick.
    EVENT_EXTERNAL_BLOCKER: 24 * 3600,
    EVENT_HUMAN_REQUIRED: 24 * 3600,
    EVENT_CIRCUIT_BREAKER_OPEN: 12 * 3600,
    EVENT_BUMP_NOTABLE: 20 * 3600,
}

MAX_OUTBOX_EVENTS = 500
MAX_VALUE_LEN = 200

# -- secret scrubbing ----------------------------------------------------------
FIELDS = (
    "level",
    "platform",
    "program",
    "event",
    "field",
    "old_value",
    "new_value",
    "source",
    "action",
    "result",
    "post_match",
    "exact",
    "immutable",
    "pc_required",
    "block_reason",
    "timestamp",
    "run_id",
)

_SECRET_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "session_id",
    "authorization",
    "bearer ",
    "api_key",
    "apikey",
    "storage-state",
    "set-cookie",
)
# Long opaque blobs are treated as credentials regardless of their label.
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_\-]{40,}")
REDACTED = "[REDACTED]"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _disabled() -> bool:
    return (os.environ.get("AUTOFRESH_NOTIFY_DISABLED") or "").strip() in {"1", "true", "TRUE"}


def scrub(value: Any) -> Any:
    """Return a Telegram-safe value: no credentials, bounded length."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    lowered = text.lower()
    if any(hint in lowered for hint in _SECRET_HINTS):
        return REDACTED
    if _OPAQUE_RE.search(text):
        return REDACTED
    if len(text) > MAX_VALUE_LEN:
        return text[: MAX_VALUE_LEN - 1] + "…"
    return text


def should_notify(event: str, level: str | None = None) -> bool:
    """Allow-list gate. Unknown or explicitly-silent events are never sent."""
    ev = (event or "").strip().lower()
    if not ev or ev in NEVER_NOTIFY_EVENTS:
        return False
    if level is not None and str(level).strip().upper() not in LEVELS:
        return False
    return ev in NOTIFIABLE_EVENTS


def dedup_key(record: dict[str, Any]) -> str:
    parts = [
        str(record.get(k) or "")
        for k in ("level", "platform", "program", "event", "field", "new_value", "block_reason")
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def dedup_ttl_seconds(event: str) -> int:
    return EVENT_DEDUP_TTL_SECONDS.get((event or "").strip().lower(), DEFAULT_DEDUP_TTL_SECONDS)


def _load_dedup() -> dict[str, str]:
    try:
        if DEDUP_PATH.exists():
            data = json.loads(DEDUP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save_dedup(state: dict[str, str]) -> None:
    DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def is_duplicate(record: dict[str, Any], *, now: datetime | None = None) -> bool:
    """True when an identical event was already emitted inside its TTL."""
    now = now or _now()
    key = dedup_key(record)
    last = _load_dedup().get(key)
    if not last:
        return False
    try:
        previous = datetime.fromisoformat(last)
    except ValueError:
        return False
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return (now - previous).total_seconds() < dedup_ttl_seconds(record.get("event", ""))


def build_event(
    level: str,
    event: str,
    *,
    platform: str | None = None,
    program: str | None = None,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    source: str | None = None,
    action: str | None = None,
    result: str | None = None,
    post_match: bool | None = None,
    exact: bool | None = None,
    immutable: bool | None = None,
    pc_required: bool | None = None,
    block_reason: str | None = None,
    run_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build one scrubbed event record. Pure: writes nothing, never raises."""
    raw = {
        "level": str(level or "").strip().upper(),
        "platform": platform,
        "program": program,
        "event": (event or "").strip().lower(),
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "source": source,
        "action": action,
        "result": result,
        "post_match": post_match,
        "exact": exact,
        "immutable": immutable,
        "pc_required": pc_required,
        "block_reason": block_reason,
        "timestamp": timestamp or _now().isoformat(),
        "run_id": run_id or os.environ.get("GITHUB_RUN_ID") or None,
    }
    record = {"schema_version": SCHEMA_VERSION}
    for key in FIELDS:
        record[key] = scrub(raw.get(key))
    return record


def _append_outbox(record: dict[str, Any]) -> None:
    NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
    with OUTBOX_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _trim_outbox()


def _trim_outbox() -> None:
    try:
        lines = OUTBOX_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    if len(lines) <= MAX_OUTBOX_EVENTS:
        return
    OUTBOX_PATH.write_text(
        "\n".join(lines[-MAX_OUTBOX_EVENTS:]) + "\n", encoding="utf-8"
    )


def emit(level: str, event: str, **fields: Any) -> dict[str, Any] | None:
    """Record one notifiable event. BEST_EFFORT / FAIL_OPEN — never raises.

    Returns the stored record, or None when the event was filtered out,
    deduplicated, disabled, or could not be written. A None return is never
    an error the caller should react to: business writes and bumps must
    proceed regardless of the notification path's health.
    """
    try:
        if _disabled():
            return None
        if not should_notify(event, level):
            return None
        record = build_event(level, event, **fields)
        if record["level"] not in LEVELS:
            return None
        now = _now()
        if is_duplicate(record, now=now):
            return None
        _append_outbox(record)
        state = _load_dedup()
        state[dedup_key(record)] = now.isoformat()
        _save_dedup(state)
        return record
    except Exception:
        # FAIL_OPEN: observability must never break the business path.
        return None


def read_events(*, since: datetime | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Read stored events oldest-first. Never raises."""
    out: list[dict[str, Any]] = []
    try:
        if not OUTBOX_PATH.exists():
            return []
        for line in OUTBOX_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            if since is not None:
                try:
                    ts = datetime.fromisoformat(str(rec.get("timestamp")))
                except Exception:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < since:
                    continue
            out.append(rec)
    except Exception:
        return out
    if limit is not None and limit >= 0:
        return out[-limit:]
    return out


def build_daily_summary(*, hours: int = 24, now: datetime | None = None) -> dict[str, Any]:
    """Optional digest of the last `hours`. Pure read; never raises."""
    now = now or _now()
    since = now - timedelta(hours=hours)
    events = read_events(since=since)
    by_level: dict[str, int] = {}
    by_event: dict[str, int] = {}
    platforms_blocked: set[str] = set()
    human_required: list[str] = []
    errors: list[str] = []
    verified_writes = 0
    bumps = 0
    changes = 0
    for rec in events:
        lvl = str(rec.get("level") or "")
        ev = str(rec.get("event") or "")
        by_level[lvl] = by_level.get(lvl, 0) + 1
        by_event[ev] = by_event.get(ev, 0) + 1
        plat = str(rec.get("platform") or "") or None
        if ev == EVENT_POST_VERIFY_SUCCESS:
            verified_writes += 1
        elif ev == EVENT_BUMP_NOTABLE:
            bumps += 1
        elif ev == EVENT_MONITOR_REAL_SAFE_DIFF:
            changes += 1
        if lvl == LEVEL_ERROR:
            errors.append(f"{plat or 'global'}: {rec.get('block_reason') or ev}")
        if lvl == LEVEL_HUMAN_REQUIRED:
            human_required.append(f"{plat or 'global'}: {rec.get('block_reason') or ev}")
        if ev in {EVENT_EXTERNAL_BLOCKER, EVENT_CIRCUIT_BREAKER_OPEN} and plat:
            platforms_blocked.add(plat)

    autonomous: list[str] = []
    blocked: list[str] = []
    try:
        from lib.write_status import ALL_PLATFORMS, may_auto_execute_on_safe_diff, runtime_route

        for pid in ALL_PLATFORMS:
            if may_auto_execute_on_safe_diff(pid):
                autonomous.append(pid)
            elif runtime_route(pid) in {
                "HUMAN_SAVE_REQUIRED",
                "NEVER_AUTO_COMMIT",
                "AUTH_BLOCKED_MANUAL",
            }:
                blocked.append(pid)
    except Exception:
        pass

    return {
        "schema_version": SCHEMA_VERSION,
        "window_hours": hours,
        "since": since.isoformat(),
        "generated_at": now.isoformat(),
        "event_count": len(events),
        "by_level": by_level,
        "by_event": by_event,
        "verified_writes": verified_writes,
        "bumps": bumps,
        "detected_changes": changes,
        "errors": errors,
        "human_required": human_required,
        "autonomous_platforms": autonomous,
        "blocked_platforms": blocked,
        "externally_blocked_in_window": sorted(platforms_blocked),
    }


def format_summary_text(summary: dict[str, Any]) -> str:
    """Short French digest Hermes can relay verbatim."""
    lines = [
        f"AutoFresh — résumé {summary.get('window_hours', 24)} h",
        f"  événements       : {summary.get('event_count', 0)}",
        f"  writes vérifiés  : {summary.get('verified_writes', 0)}",
        f"  bumps notables   : {summary.get('bumps', 0)}",
        f"  changements      : {summary.get('detected_changes', 0)}",
        f"  erreurs          : {len(summary.get('errors') or [])}",
        f"  humain requis    : {len(summary.get('human_required') or [])}",
        f"  autonomes        : {', '.join(summary.get('autonomous_platforms') or []) or '—'}",
        f"  bloquées         : {', '.join(summary.get('blocked_platforms') or []) or '—'}",
    ]
    return "\n".join(lines)


def pending_for_hermes(limit: int = 20) -> list[dict[str, Any]]:
    """Recent events for the Hermes result payload. Never raises."""
    return read_events(limit=limit)


def iter_levels() -> Iterable[str]:
    return LEVELS
