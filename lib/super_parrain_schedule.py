"""Super-Parrain: cycle historique ~24h + couche Autofresh PRE-BUMP.

Le bumper reste la fonction principale.
Autofresh se greffe AVANT la remontee, ne la remplace pas, et ne bloque
jamais indefiniment le bump via un pending.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR, ROOT
from lib.sync_state import SyncStateStore

COOLDOWN = timedelta(hours=24)
LAST_SUPER_RUN = ROOT / "last_super_run.txt"
PENDING_PATH = DATA_DIR / "pending_writes.json"
CYCLE_REPORT = DATA_DIR / "captures" / "super-parrain-last-cycle.json"


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
    if not LAST_SUPER_RUN.exists():
        return None
    dt = _parse_dt(LAST_SUPER_RUN.read_text(encoding="utf-8").strip())
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def next_eligible_at(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    last = last_super_action_at()
    if last is None:
        return now
    return last + COOLDOWN


def is_eligible(now: datetime | None = None) -> tuple[bool, datetime, float]:
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
    """File d'attente informative pour le pre-check — ne bloque PAS le bump."""
    data = load_pending()
    items = data.setdefault("items", [])
    key = f"{platform}:{program}:{language}"
    _, nxt, hours = is_eligible()
    for it in items:
        if it.get("key") == key and it.get("status") == "pending":
            it["reason"] = reason
            it["updated_at"] = datetime.now(timezone.utc).isoformat()
            it["next_eligible_at"] = nxt.isoformat()
            it["hours_remaining"] = round(hours, 2)
            save_pending(data)
            return it
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
        "blocks_bump": False,
    }
    items.append(item)
    SyncStateStore().upsert_entry(
        platform,
        program,
        language,
        {
            "status": "pending_content_at_next_slot",
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
    """Decide l'action du cron historique Super-Parrain.

    - wait: hors creneau 24h (comportement bumper historique)
    - cycle: creneau atteint → PRE-CHECK Autofresh puis bump normal

    Un pending content update NE bloque PAS le bump: il est traite AU creneau,
    avant la remontee.
    """
    eligible, nxt, hours = is_eligible()
    pending = list_pending_super_parrain()
    if not eligible:
        return {
            "action": "wait",
            "reason": "cooldown_24h_historical_slot",
            "next_eligible_at": nxt.isoformat(),
            "hours_remaining": round(hours, 2),
            "pending_count": len(pending),
            "skip_bump": True,  # hors creneau, comme bumper.py aujourd'hui
            "run_precheck": False,
            "run_bump": False,
        }
    return {
        "action": "cycle",
        "reason": "slot_reached_precheck_then_bump",
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": 0,
        "pending_count": len(pending),
        "skip_bump": False,
        "run_precheck": True,
        "run_bump": True,
        "pending_programs": [p.get("program") for p in pending],
    }


def record_super_action_now() -> None:
    LAST_SUPER_RUN.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def save_cycle_report(report: dict[str, Any]) -> Path:
    CYCLE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    report["at"] = datetime.now(timezone.utc).isoformat()
    CYCLE_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return CYCLE_REPORT
