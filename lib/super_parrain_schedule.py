"""Coordination Super-Parrain: content update pending > bump + cooldown 24h.

Le cooldown plateforme ne peut PAS etre contourne par --force.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR, ROOT, SYNC_STATE_PATH, sync_entry_key
from lib.sync_state import SyncStateStore

COOLDOWN = timedelta(hours=24)
LAST_SUPER_RUN = ROOT / "last_super_run.txt"
PENDING_PATH = DATA_DIR / "pending_writes.json"


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def last_super_action_at() -> datetime | None:
    """Derniere action codes-promo (bump ou write) connue localement."""
    candidates: list[datetime] = []
    if LAST_SUPER_RUN.exists():
        dt = _parse_dt(LAST_SUPER_RUN.read_text(encoding="utf-8").strip())
        if dt:
            candidates.append(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
    # Also check pending / sync state last attempt
    store = SyncStateStore()
    data = store.load()
    for entry in (data.get("entries") or {}).values():
        if entry.get("platform") != "super-parrain":
            continue
        for key in ("last_write_at", "last_attempt_at", "last_success_at"):
            dt = _parse_dt(entry.get(key))
            if dt:
                candidates.append(dt)
    if not candidates:
        return None
    return max(candidates)


def next_eligible_at(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    last = last_super_action_at()
    if last is None:
        return now
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last + COOLDOWN


def is_eligible(now: datetime | None = None) -> tuple[bool, datetime, float]:
    """Returns (eligible, next_eligible_at, hours_remaining)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    nxt = next_eligible_at(now)
    remaining = (nxt - now).total_seconds() / 3600.0
    return remaining <= 0, nxt, max(0.0, remaining)


def load_pending() -> dict[str, Any]:
    if not PENDING_PATH.exists():
        return {"version": 1, "items": []}
    return json.loads(PENDING_PATH.read_text(encoding="utf-8"))


def save_pending(data: dict[str, Any]) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    PENDING_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def list_pending_super_parrain() -> list[dict[str, Any]]:
    data = load_pending()
    return [
        i
        for i in data.get("items") or []
        if i.get("platform") == "super-parrain" and i.get("status") == "pending"
    ]


def enqueue_pending(
    platform: str,
    program: str,
    language: str = "fr",
    reason: str = "content_update",
) -> dict[str, Any]:
    data = load_pending()
    items = data.setdefault("items", [])
    key = f"{platform}:{program}:{language}"
    for it in items:
        if it.get("key") == key and it.get("status") == "pending":
            it["reason"] = reason
            it["updated_at"] = datetime.now(timezone.utc).isoformat()
            eligible, nxt, hours = is_eligible()
            it["next_eligible_at"] = nxt.isoformat()
            it["hours_remaining"] = round(hours, 2)
            save_pending(data)
            return it
    eligible, nxt, hours = is_eligible()
    item = {
        "key": key,
        "platform": platform,
        "program": program,
        "language": language,
        "status": "pending",
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2),
        "priority": 100 if platform == "super-parrain" else 50,
    }
    items.append(item)
    # Sync state mirror
    SyncStateStore().upsert_entry(
        platform,
        program,
        language,
        {
            "status": "pending_write",
            "next_eligible_at": nxt.isoformat(),
            "pending_reason": reason,
        },
    )
    save_pending(data)
    return item


def mark_pending_done(platform: str, program: str, language: str = "fr") -> None:
    data = load_pending()
    key = f"{platform}:{program}:{language}"
    for it in data.get("items") or []:
        if it.get("key") == key and it.get("status") == "pending":
            it["status"] = "done"
            it["done_at"] = datetime.now(timezone.utc).isoformat()
    save_pending(data)


def decide_super_parrain_action() -> dict[str, Any]:
    """Decide si le cron Super-Parrain doit bump, write, ou skip.

    Returns action: write | bump | wait
    """
    eligible, nxt, hours = is_eligible()
    pending = list_pending_super_parrain()
    # Also treat dry-run pending_update for known programs as soft pending
    # (explicit queue is authoritative)
    if pending:
        top = sorted(pending, key=lambda x: -int(x.get("priority") or 0))[0]
        if not eligible:
            return {
                "action": "wait",
                "reason": "content_update_pending_but_cooldown_active",
                "pending": top,
                "next_eligible_at": nxt.isoformat(),
                "hours_remaining": round(hours, 2),
                "skip_bump": True,
            }
        return {
            "action": "write",
            "reason": "content_update_takes_priority",
            "pending": top,
            "next_eligible_at": nxt.isoformat(),
            "hours_remaining": 0,
            "skip_bump": True,
            "program": top.get("program"),
            "language": top.get("language") or "fr",
        }
    if not eligible:
        return {
            "action": "wait",
            "reason": "cooldown_active_no_pending_update",
            "next_eligible_at": nxt.isoformat(),
            "hours_remaining": round(hours, 2),
            "skip_bump": True,
        }
    return {
        "action": "bump",
        "reason": "no_pending_content_update",
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": 0,
        "skip_bump": False,
    }


def record_super_action_now() -> None:
    """Enregistre qu'une action codes-promo vient d'avoir lieu (bump ou write)."""
    LAST_SUPER_RUN.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
