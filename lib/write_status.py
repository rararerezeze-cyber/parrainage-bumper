"""Strict platform write readiness.

WRITE_VERIFIED is NEVER inferred from "writer prepared" or "canary armed".

Required for WRITE_VERIFIED:
  authenticated account
  + targeted edit
  + successful submit
  + reread account/edit
  + reread public if available
  + expected values present
  + immutable preserved
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR

STATUS_PATH = DATA_DIR / "platform-write-status.json"

# Allowed statuses (strict ladder)
STATUS_UNPREPARED = "UNPREPARED"
STATUS_WRITE_PREPARED = "WRITE_PREPARED"
STATUS_CANARY_READY = "CANARY_READY"
STATUS_WRITE_VERIFIED = "WRITE_VERIFIED"
STATUS_AUTH_BLOCKED = "AUTH_BLOCKED_GOOGLE"
STATUS_MANUAL_ONLY = "MANUAL_ONLY"
STATUS_FAILED = "CANARY_FAILED"

ALL_PLATFORMS = (
    "super-parrain",
    "parrainage-co",
    "code-parrainage",
    "1parrainage",
    "referralcodes",
    "referralcode-tv",
    "referraldrop",
)

# Default inventory — honest baseline after code review (no live proof of WRITE_VERIFIED)
DEFAULT_STATUS: dict[str, dict[str, Any]] = {
    "super-parrain": {
        "status": STATUS_CANARY_READY,
        "canary_program": "kraken",
        "notes": (
            "Writer + post-verify + cooldown ready. "
            "Not WRITE_VERIFIED until real auth edit + submit + reread + field post-match."
        ),
    },
    "parrainage-co": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "notes": "Controlled writer exists; needs one successful canary with post-verify.",
    },
    "code-parrainage": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "notes": "Templates/mappings ready; live edit canary not completed.",
    },
    "1parrainage": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "notes": "Templates/mappings ready; live edit canary not completed.",
    },
    "referralcodes": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "notes": (
            "Official Agent Import preferred. WRITE_VERIFIED only after schema validation "
            "+ single canary import + reread post-match."
        ),
        "prefer": "official_import",
    },
    "referralcode-tv": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "notes": (
            "Sequential browser writer prepared. WRITE_VERIFIED only after auth/edit "
            "with REFERRALCODE_* + post-verify. No CAPTCHA bypass."
        ),
    },
    "referraldrop": {
        "status": STATUS_AUTH_BLOCKED,
        "canary_program": None,
        "notes": (
            "Google Sign-In required. No OAuth bypass. "
            "May remain non-automated if legitimate durable automation is impossible."
        ),
    },
}

REQUIRED_VERIFY_CHECKS = (
    "authenticated",
    "targeted_edit",
    "submit_ok",
    "reread_account",
    "expected_values_present",
    "immutable_preserved",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_write_status() -> dict[str, Any]:
    if STATUS_PATH.exists():
        try:
            raw = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("platforms"):
                return raw
        except Exception:
            pass
    return {
        "version": 1,
        "updated_at": None,
        "note": (
            "Strict write readiness. WRITE_VERIFIED requires full post-verify evidence. "
            "Telegram live writes only for WRITE_VERIFIED platforms."
        ),
        "platforms": {k: dict(v) for k, v in DEFAULT_STATUS.items()},
    }


def save_write_status(data: dict[str, Any]) -> Path:
    data = dict(data)
    data["updated_at"] = _now()
    data["write_verified_count"] = sum(
        1
        for p, meta in (data.get("platforms") or {}).items()
        if (meta or {}).get("status") == STATUS_WRITE_VERIFIED
    )
    data["telegram_live_capable"] = [
        p
        for p, meta in (data.get("platforms") or {}).items()
        if (meta or {}).get("status") == STATUS_WRITE_VERIFIED
    ]
    data["write_verified_ratio"] = f"{data['write_verified_count']}/7"
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return STATUS_PATH


def ensure_write_status_file() -> dict[str, Any]:
    data = load_write_status()
    # merge defaults for missing platforms
    plats = data.setdefault("platforms", {})
    for pid, meta in DEFAULT_STATUS.items():
        if pid not in plats:
            plats[pid] = dict(meta)
    save_write_status(data)
    return data


def get_platform_status(platform: str) -> str:
    data = load_write_status()
    meta = (data.get("platforms") or {}).get(platform.strip().lower()) or {}
    return str(meta.get("status") or STATUS_UNPREPARED)


def is_write_verified(platform: str) -> bool:
    return get_platform_status(platform) == STATUS_WRITE_VERIFIED


def is_telegram_live_capable(platform: str) -> bool:
    """Only WRITE_VERIFIED platforms receive live Telegram field updates."""
    return is_write_verified(platform)


def is_canary_ready(platform: str) -> bool:
    return get_platform_status(platform) == STATUS_CANARY_READY


def telegram_action_for_platform(platform: str) -> str:
    """What Telegram path may do on this platform."""
    st = get_platform_status(platform)
    if st == STATUS_WRITE_VERIFIED:
        return "LIVE_UPDATE"
    if st == STATUS_CANARY_READY:
        return "CANARY_ONLY"  # explicit canary tool, not bulk telegram auto
    if st == STATUS_AUTH_BLOCKED:
        return "AUTH_BLOCKED"
    if st == STATUS_WRITE_PREPARED:
        return "PLAN_ONLY"
    return "PLAN_ONLY"


def mark_canary_ready(platform: str, *, canary_program: str = "kraken", notes: str | None = None) -> None:
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    if meta.get("status") == STATUS_WRITE_VERIFIED:
        save_write_status(data)
        return
    meta["status"] = STATUS_CANARY_READY
    meta["canary_program"] = canary_program
    if notes:
        meta["notes"] = notes
    save_write_status(data)


def mark_write_verified(
    platform: str,
    *,
    program: str,
    evidence: dict[str, Any],
    force: bool = False,
) -> dict[str, Any]:
    """Promote to WRITE_VERIFIED only with complete evidence.

    evidence must include required boolean checks and preferably URLs/timestamps.
    """
    checks = evidence.get("checks") or {}
    missing = [k for k in REQUIRED_VERIFY_CHECKS if not checks.get(k)]
    if missing and not force:
        return {
            "ok": False,
            "error": "incomplete_evidence",
            "missing_checks": missing,
            "required": list(REQUIRED_VERIFY_CHECKS),
        }
    if not evidence.get("post_match") and not force:
        return {"ok": False, "error": "post_match_required"}

    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    meta["status"] = STATUS_WRITE_VERIFIED
    meta["canary_program"] = program
    meta["last_write_verified_at"] = _now()
    meta["evidence"] = {
        "program": program,
        "checks": checks,
        "post_match": evidence.get("post_match"),
        "announcement_url": evidence.get("announcement_url"),
        "edit_url": evidence.get("edit_url"),
        "public_reread": evidence.get("public_reread"),
        "immutable_ok": evidence.get("immutable_ok", checks.get("immutable_preserved")),
        "recorded_at": _now(),
        "source": evidence.get("source") or "canary",
    }
    save_write_status(data)

    # keep phase.json in sync for live_writes_enabled
    from lib.phase import mark_write_verified as phase_mark

    phase_mark(platform)
    return {"ok": True, "platform": platform, "status": STATUS_WRITE_VERIFIED}


def mark_canary_failed(platform: str, error: str, *, program: str | None = None) -> None:
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    # stay CANARY_READY / PREPARED — don't claim verified
    if meta.get("status") == STATUS_WRITE_VERIFIED:
        # demote only on explicit failure after verified? keep verified, log last failure
        meta["last_failure"] = {"error": error, "program": program, "at": _now()}
    else:
        if meta.get("status") not in {STATUS_AUTH_BLOCKED, STATUS_MANUAL_ONLY}:
            # remain canary_ready or prepared
            pass
        meta["last_failure"] = {"error": error, "program": program, "at": _now()}
    save_write_status(data)


def summary() -> dict[str, Any]:
    data = ensure_write_status_file()
    by_status: dict[str, int] = {}
    rows = []
    for pid in ALL_PLATFORMS:
        meta = (data.get("platforms") or {}).get(pid) or {}
        st = meta.get("status") or STATUS_UNPREPARED
        by_status[st] = by_status.get(st, 0) + 1
        rows.append(
            {
                "platform": pid,
                "status": st,
                "telegram_action": telegram_action_for_platform(pid),
                "canary_program": meta.get("canary_program"),
                "notes": meta.get("notes"),
                "last_write_verified_at": meta.get("last_write_verified_at"),
            }
        )
    return {
        "WRITE_VERIFIED": f"{data.get('write_verified_count', 0)}/7",
        "write_verified_count": data.get("write_verified_count", 0),
        "telegram_live_capable": data.get("telegram_live_capable") or [],
        "telegram_live_capable_count": len(data.get("telegram_live_capable") or []),
        "by_status": by_status,
        "platforms": rows,
        "updated_at": data.get("updated_at"),
    }


def format_telegram_platform_lines(plan_platforms: list[dict[str, Any]] | None = None) -> list[str]:
    """Build per-platform status lines for Telegram replies."""
    lines = []
    impacts = {p.get("platform"): p for p in (plan_platforms or [])}
    for pid in ALL_PLATFORMS:
        st = get_platform_status(pid)
        action = telegram_action_for_platform(pid)
        imp = impacts.get(pid) or {}
        ch = imp.get("changed_fields") or {}
        if st == STATUS_WRITE_VERIFIED and imp.get("status") == "pending_update":
            label = "UPDATED + VERIFIED" if action == "LIVE_UPDATE" else "PLAN (verified)"
        elif st == STATUS_WRITE_VERIFIED and imp.get("status") == "in_sync":
            label = "IN_SYNC + VERIFIED"
        elif st == STATUS_CANARY_READY:
            label = "CANARY_READY / PLAN_ONLY"
        elif st == STATUS_AUTH_BLOCKED:
            label = "AUTH_BLOCKED"
        elif st == STATUS_WRITE_PREPARED:
            label = "PLAN_ONLY"
        else:
            label = st
        extra = ""
        if ch:
            extra = " " + ",".join(list(ch.keys())[:3])
        lines.append(f"  {pid:18} {label}{extra}")
    return lines
