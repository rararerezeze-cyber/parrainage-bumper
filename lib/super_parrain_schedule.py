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


def is_super_parrain_canary_pending() -> bool:
    """True until Super-Parrain content canary is WRITE_VERIFIED.

    While True, the historical bumper must NOT consume the 24h slot —
    the content canary has exclusive priority on the next eligible window.
    """
    try:
        from lib.write_status import is_write_verified

        return not is_write_verified("super-parrain")
    except Exception:
        # Fail-safe: reserve slot for canary if status cannot be read
        return True


def super_parrain_runtime_mode() -> str:
    """CANARY_PENDING | NORMAL_BUMP"""
    return "CANARY_PENDING" if is_super_parrain_canary_pending() else "NORMAL_BUMP"


def decide_super_parrain_action() -> dict[str, Any]:
    """Decide l'action du cron *historique* bump_super_parrain.yml.

    Shared gate with activation_canary.yml (concurrency ``parrainage-bumper-super``):

    - CANARY_PENDING (not WRITE_VERIFIED):
        → action ``skip`` always for the historical bumper
        → never bump / never save / never consume last_super_run
        → activation_canary.yml is the *only* workflow allowed to save
    - wait: hors créneau 24h (also skip_bump)
    - cycle: WRITE_VERIFIED + créneau ouvert → PRE-CHECK + bumper normal

    Does not delete the bumper — only suspends its saves until canary succeeds.
    """
    eligible, nxt, hours = is_eligible()
    pending = list_pending_super_parrain()
    mode = super_parrain_runtime_mode()
    canary_pending = mode == "CANARY_PENDING"
    base = {
        "runtime_mode": mode,
        "canary_pending": canary_pending,
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2) if not eligible else 0.0,
        "pending_count": len(pending),
        "pending_programs": [p.get("program") for p in pending],
        "activation_canary_owns_save": canary_pending,
    }

    # Hard gate: historical bumper is suspended until WRITE_VERIFIED
    if canary_pending:
        return {
            **base,
            "action": "skip",
            "reason": (
                "CANARY_PENDING_historical_bump_suspended"
                if eligible
                else "canary_pending_cooldown"
            ),
            "skip_bump": True,
            "run_precheck": False,
            "run_bump": False,
            "run_canary": False,
            "eligible_now": eligible,
            "note": (
                "bump_super_parrain.yml SKIP while CANARY_PENDING. "
                "Only activation_canary.yml may live-save (Kraken content canary). "
                "After post_match=true → WRITE_VERIFIED → NORMAL_BUMP re-enabled."
            ),
        }

    if not eligible:
        return {
            **base,
            "action": "wait",
            "reason": "cooldown_24h_historical_slot",
            "skip_bump": True,
            "run_precheck": False,
            "run_bump": False,
            "run_canary": False,
            "eligible_now": False,
        }

    return {
        **base,
        "action": "cycle",
        "reason": "slot_reached_write_verified_precheck_then_bump",
        "skip_bump": False,
        "run_precheck": True,
        "run_bump": True,
        "run_canary": False,
        "eligible_now": True,
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
