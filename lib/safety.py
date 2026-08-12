"""Snapshot, rollback, audit log, and circuit breakers.

Never stores secrets. Does not touch last_super_run / Super-Parrain cooldown.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR

SNAPSHOT_DIR = DATA_DIR / "snapshots"
AUDIT_PATH = DATA_DIR / "audit" / "events.jsonl"
CIRCUIT_PATH = DATA_DIR / "circuit-breakers.json"

SNAPSHOT_FILES = (
    "operator-overrides.json",
    "platform-write-status.json",
    "pending_writes.json",
    "autofresh-phase.json",
    "circuit-breakers.json",
    "sync-state.json",
)

STOP_KINDS = frozenset(
    {
        "403",
        "429",
        "CAPTCHA",
        "auth",
        "unexpected_dom",
        "rate_limit",
        "challenge",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(event: str, **fields: Any) -> None:
    rec = {"at": _now(), "event": event, **fields}
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_circuits() -> dict[str, Any]:
    if CIRCUIT_PATH.exists():
        try:
            data = json.loads(CIRCUIT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "version": 1,
        "updated_at": None,
        "global_open": False,
        "global_reason": None,
        "platforms": {},
        "rules": {
            "stop_on": sorted(STOP_KINDS),
            "mass_change_guard": True,
            "no_auto_accept_monitor": True,
            "no_live_write_while_canary_pending_super": True,
        },
    }


def save_circuits(data: dict[str, Any]) -> Path:
    data = dict(data)
    data["updated_at"] = _now()
    CIRCUIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CIRCUIT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CIRCUIT_PATH


def is_circuit_open(platform: str | None = None) -> tuple[bool, str | None]:
    data = load_circuits()
    if data.get("global_open"):
        return True, str(data.get("global_reason") or "global_circuit_open")
    if platform:
        meta = (data.get("platforms") or {}).get(platform) or {}
        if meta.get("open"):
            return True, str(meta.get("reason") or f"circuit_open:{platform}")
    return False, None


def trip_circuit(reason: str, *, platform: str | None = None, kind: str | None = None) -> dict[str, Any]:
    data = load_circuits()
    rec = {"open": True, "reason": reason, "kind": kind, "at": _now()}
    if platform:
        plats = data.setdefault("platforms", {})
        plats[platform] = rec
    else:
        data["global_open"] = True
        data["global_reason"] = reason
    save_circuits(data)
    audit("circuit_open", platform=platform, reason=reason, kind=kind)
    return rec


def reset_circuit(*, platform: str | None = None) -> None:
    data = load_circuits()
    if platform:
        plats = data.setdefault("platforms", {})
        if platform in plats:
            plats[platform] = {"open": False, "reason": None, "reset_at": _now()}
    else:
        data["global_open"] = False
        data["global_reason"] = None
    save_circuits(data)
    audit("circuit_reset", platform=platform)


def classify_stop(message: str, *, status_code: int | None = None) -> str | None:
    """Return a stop kind if the writer/monitor must halt."""
    msg = (message or "").lower()
    if status_code == 429 or "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "429"
    if status_code == 403 or " 403" in f" {msg}" or "forbidden" in msg:
        return "403"
    if any(
        x in msg
        for x in (
            "captcha",
            "recaptcha",
            "hcaptcha",
            "turnstile",
            "challenge",
            "cloudflare",
            "verify you are human",
        )
    ):
        return "CAPTCHA"
    if any(x in msg for x in ("login failed", "login echoue", "auth_required", "unauthorized", "401")):
        return "auth"
    if "unexpected_dom" in msg or "aucun champ editable" in msg or "bouton" in msg and "introuvable" in msg:
        return "unexpected_dom"
    return None


def maybe_trip_from_error(message: str, *, platform: str | None = None, status_code: int | None = None) -> str | None:
    kind = classify_stop(message, status_code=status_code)
    if kind:
        trip_circuit(message[:300], platform=platform, kind=kind)
    return kind


def snapshot_state(reason: str) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = SNAPSHOT_DIR / stamp
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    missing: list[str] = []
    for name in SNAPSHOT_FILES:
        src = DATA_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)
        else:
            missing.append(name)
    meta = {
        "id": stamp,
        "at": _now(),
        "reason": reason,
        "copied": copied,
        "missing": missing,
        "path": str(dest),
        "excludes": ["last_super_run.txt", "secrets", "cookies", "storage-state"],
    }
    (dest / "snapshot-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit("snapshot", snapshot_id=stamp, reason=reason, copied=copied)
    return meta


def list_snapshots() -> list[dict[str, Any]]:
    if not SNAPSHOT_DIR.exists():
        return []
    out = []
    for p in sorted(SNAPSHOT_DIR.iterdir()):
        meta = p / "snapshot-meta.json"
        if meta.exists():
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except Exception:
                out.append({"id": p.name, "path": str(p)})
    return out


def rollback_snapshot(snapshot_id: str) -> dict[str, Any]:
    dest = SNAPSHOT_DIR / snapshot_id
    if not dest.is_dir():
        return {"ok": False, "error": "snapshot_not_found", "id": snapshot_id}
    restored = []
    for name in SNAPSHOT_FILES:
        src = dest / name
        if src.exists():
            shutil.copy2(src, DATA_DIR / name)
            restored.append(name)
    audit("rollback", snapshot_id=snapshot_id, restored=restored)
    return {"ok": True, "id": snapshot_id, "restored": restored}


def live_write_blocked_reason(platform: str | None = None) -> str | None:
    open_, reason = is_circuit_open(platform)
    if open_:
        return reason
    return None
