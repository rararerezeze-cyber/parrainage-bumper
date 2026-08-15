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


# --- Historical bumper authorization -------------------------------------
#
# Deliberately independent from WRITE_VERIFIED. WRITE_VERIFIED proves one
# targeted content-writer canary works (login + edit + save + reread +
# post_match on a single program, e.g. Poulpeo — see
# data/captures/write-super-parrain-poulpeo.json). It must never by itself
# authorize the *global* historical bumper, which opens every codes-promo
# edit form across ~35 programs and clicks Enregistrer once per program.
# These are two different blast radii and must be gated independently.
#
# Fail-closed: with no explicit operator action, this is always False, even
# once WRITE_VERIFIED is true. Only authorize_historical_bumper() may set it
# True; only revoke_historical_bumper_authorization() (or never calling
# authorize in the first place) keeps/returns it to False.
RUNTIME_MODE_CANARY_PENDING = "CANARY_PENDING"
RUNTIME_MODE_BUMPER_SUSPENDED = "WRITE_VERIFIED_BUMPER_SUSPENDED"
RUNTIME_MODE_NORMAL_BUMP = "NORMAL_BUMP"


def is_historical_bumper_authorized() -> bool:
    try:
        from lib.phase import load_phase

        return bool(load_phase().get("super_parrain_historical_bumper_authorized"))
    except Exception:
        # Fail-closed: unreadable state must never be read as "authorized".
        return False


def _sync_write_status_bumper_label(*, bumper_authorized: bool) -> None:
    """Keep platform-write-status.json's display fields (runtime_mode,
    autonomy) in sync with the authorization flag whenever super-parrain is
    already WRITE_VERIFIED. Never touches status/evidence/timestamps -- this
    is a label refresh, not a new verification event.
    """
    try:
        from lib.write_status import (
            AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED,
            STATUS_WRITE_VERIFIED,
            load_write_status,
            save_write_status,
        )

        data = load_write_status()
        meta = (data.get("platforms") or {}).get("super-parrain") or {}
        if meta.get("status") != STATUS_WRITE_VERIFIED:
            return
        meta["runtime_mode"] = "NORMAL_BUMP" if bumper_authorized else "WRITE_VERIFIED_BUMPER_SUSPENDED"
        meta["autonomy"] = (
            "FUSED_UPDATE_BUMP" if bumper_authorized else AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED
        )
        data["platforms"]["super-parrain"] = meta
        save_write_status(data)
    except Exception:
        pass


def authorize_historical_bumper(reason: str, *, actor: str = "operator") -> dict[str, Any]:
    """Explicit, auditable operator action. Nothing else may set this True.

    Does not touch last_super_run.txt / cooldown — authorization only lifts
    the WRITE_VERIFIED_BUMPER_SUSPENDED gate; the normal 24h cooldown still
    applies on top before any real cycle runs.
    """
    from lib.phase import load_phase, save_phase
    from lib.safety import audit, snapshot_state

    snapshot_state("authorize_historical_bumper")
    data = load_phase()
    data["super_parrain_historical_bumper_authorized"] = True
    data["super_parrain_historical_bumper_authorized_at"] = datetime.now(timezone.utc).isoformat()
    data["super_parrain_historical_bumper_authorized_by"] = actor
    data["super_parrain_historical_bumper_authorized_reason"] = reason
    save_phase(data)
    _sync_write_status_bumper_label(bumper_authorized=True)
    audit("historical_bumper_authorized", actor=actor, reason=reason)
    return {"ok": True, "authorized": True, "actor": actor, "reason": reason}


def revoke_historical_bumper_authorization(*, reason: str = "", actor: str = "operator") -> dict[str, Any]:
    from lib.phase import load_phase, save_phase
    from lib.safety import audit, snapshot_state

    snapshot_state("revoke_historical_bumper_authorization")
    data = load_phase()
    data["super_parrain_historical_bumper_authorized"] = False
    data["super_parrain_historical_bumper_revoked_at"] = datetime.now(timezone.utc).isoformat()
    data["super_parrain_historical_bumper_revoked_by"] = actor
    data["super_parrain_historical_bumper_revoked_reason"] = reason
    save_phase(data)
    _sync_write_status_bumper_label(bumper_authorized=False)
    audit("historical_bumper_authorization_revoked", actor=actor, reason=reason)
    return {"ok": True, "authorized": False, "actor": actor, "reason": reason}


def super_parrain_runtime_mode() -> str:
    """CANARY_PENDING | WRITE_VERIFIED_BUMPER_SUSPENDED | NORMAL_BUMP

    CANARY_PENDING: content-writer not yet WRITE_VERIFIED.
    WRITE_VERIFIED_BUMPER_SUSPENDED: content-writer IS WRITE_VERIFIED, but
      the historical bumper has not been explicitly authorized. Fail-closed
      default — this is the common state right after a first WRITE_VERIFIED.
    NORMAL_BUMP: content-writer WRITE_VERIFIED AND historical bumper
      explicitly authorized (authorize_historical_bumper() was called).
    """
    if is_super_parrain_canary_pending():
        return RUNTIME_MODE_CANARY_PENDING
    if not is_historical_bumper_authorized():
        return RUNTIME_MODE_BUMPER_SUSPENDED
    return RUNTIME_MODE_NORMAL_BUMP


def decide_super_parrain_action() -> dict[str, Any]:
    """Decide l'action du cron *historique* bump_super_parrain.yml.

    Shared gate with activation_canary.yml (concurrency ``parrainage-bumper-super``):

    - CANARY_PENDING (not WRITE_VERIFIED):
        → action ``skip`` always for the historical bumper
        → never bump / never save / never consume last_super_run
        → activation_canary.yml is the *only* workflow allowed to save
    - WRITE_VERIFIED_BUMPER_SUSPENDED (WRITE_VERIFIED but bumper not
      explicitly authorized): action ``skip`` always. WRITE_VERIFIED proves
      one targeted content-writer canary, not that the operator wants the
      global ~35-program historical bumper to run. Fail-closed: this is the
      default the moment WRITE_VERIFIED becomes true, until
      authorize_historical_bumper() is called.
    - wait: hors créneau 24h (also skip_bump)
    - cycle: WRITE_VERIFIED + bumper authorized + créneau ouvert → PRE-CHECK + bumper normal

    Does not delete the bumper — only suspends its saves until both gates pass.
    """
    eligible, nxt, hours = is_eligible()
    pending = list_pending_super_parrain()
    mode = super_parrain_runtime_mode()
    canary_pending = mode == RUNTIME_MODE_CANARY_PENDING
    bumper_suspended = mode == RUNTIME_MODE_BUMPER_SUSPENDED
    base = {
        "runtime_mode": mode,
        "canary_pending": canary_pending,
        "next_eligible_at": nxt.isoformat(),
        "hours_remaining": round(hours, 2) if not eligible else 0.0,
        "pending_count": len(pending),
        "pending_programs": [p.get("program") for p in pending],
        "activation_canary_owns_save": canary_pending,
        "historical_bumper_authorized": is_historical_bumper_authorized(),
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
                "After post_match=true → WRITE_VERIFIED → still SUSPENDED until "
                "the historical bumper is separately authorized."
            ),
        }

    # Second, independent hard gate: WRITE_VERIFIED alone never authorizes
    # the global historical bumper. Fail-closed until an operator explicitly
    # calls authorize_historical_bumper().
    if bumper_suspended:
        return {
            **base,
            "action": "skip",
            "reason": "historical_bumper_not_authorized",
            "skip_bump": True,
            "run_precheck": False,
            "run_bump": False,
            "run_canary": False,
            "eligible_now": eligible,
            "note": (
                "super-parrain content-writer is WRITE_VERIFIED (a real targeted "
                "canary save was proven — login, edit, save, reread, post_match). "
                "The separate global historical bumper (~35 programs, 1 "
                "Enregistrer/code) requires an explicit operator authorization "
                "that has not been given. WRITE_VERIFIED never implies bumper "
                "authorization. Call "
                "lib.super_parrain_schedule.authorize_historical_bumper(reason) "
                "to lift this gate; the 24h cooldown still applies on top."
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
