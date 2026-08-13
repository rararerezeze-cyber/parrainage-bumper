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
STATUS_AUTH_BLOCKED_MANUAL = "AUTH_BLOCKED_MANUAL"
STATUS_MANUAL_ONLY = "MANUAL_ONLY"
STATUS_FAILED = "CANARY_FAILED"

# Autonomy classes (do not replace status). WRITE_VERIFIED ≠ unattended.
AUTONOMY_FULLY_UNATTENDED = "FULLY_UNATTENDED"
AUTONOMY_PC_OFF_READY = "PC_OFF_READY"
AUTONOMY_HUMAN_SAVE_REQUIRED = "HUMAN_SAVE_REQUIRED"
AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN = "IMPORT_UI_BETA_NOT_PROVEN"
AUTONOMY_AUTH_BLOCKED_MANUAL = "AUTH_BLOCKED_MANUAL"

# Content-sync flag (does NOT replace status). Super-Parrain after honest no-diff canary.
CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF = "SYNC_VERIFIED_NO_SAFE_DIFF"

# Last live/public compare class (does NOT replace status).
COMPARE_NO_SAFE_DIFF = "NO_SAFE_DIFF"
COMPARE_REAL_SAFE_DIFF = "REAL_SAFE_DIFF"
COMPARE_AUTH_BLOCKED = "AUTH_BLOCKED"
COMPARE_DOM_BLOCKED = "DOM_BLOCKED"

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
        "runtime_mode": "CANARY_PENDING",
        "notes": (
            "CANARY_PENDING: historical bumper blocked until content WRITE_VERIFIED. "
            "SYNC_VERIFIED_NO_SAFE_DIFF (if set) unblocks later platform canaries "
            "without claiming WRITE_VERIFIED."
        ),
        "content_sync": None,
    },
    "parrainage-co": {
        "status": STATUS_CANARY_READY,
        "canary_program": "kraken",
        "notes": (
            "CANARY_READY: login/edit/save/reread pipeline ready (controlled_write_parrainage_co). "
            "Not WRITE_VERIFIED until live post_match canary."
        ),
    },
    "code-parrainage": {
        "status": STATUS_CANARY_READY,
        "canary_program": "kraken",
        "autonomy": AUTONOMY_PC_OFF_READY,
        "notes": (
            "PC-off writer uses bumper.solve_slider only (same as historical bump). "
            "Not WRITE_VERIFIED until a real SAFE_DIFF save + post_match."
        ),
    },
    "1parrainage": {
        "status": STATUS_CANARY_READY,
        "canary_program": "kraken",
        "autonomy": AUTONOMY_PC_OFF_READY,
        "notes": (
            "PC-off: cookie + official /login + CKEditor + form[action*=parrainages/edit] Envoyer. "
            "No fake write."
        ),
    },
    "referralcodes": {
        "status": STATUS_CANARY_READY,
        "canary_program": "kraken",
        "autonomy": AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN,
        "notes": (
            "Official Agent Import is beta UI (Validate then Commit). "
            "Direct API is future — durable unattended R/W not proven."
        ),
        "prefer": "official_import",
        "import_ui": "https://referralcodes.com/profile/import/agent",
    },
    "referralcode-tv": {
        "status": STATUS_WRITE_PREPARED,
        "canary_program": "kraken",
        "autonomy": AUTONOMY_HUMAN_SAVE_REQUIRED,
        "save_requires_captcha": True,
        "notes": (
            "WRITE_VERIFIED headed canary still requires reCAPTCHA on Save. "
            "HUMAN_SAVE_REQUIRED. No bypass. No GH auto-save."
        ),
    },
    "referraldrop": {
        "status": STATUS_AUTH_BLOCKED_MANUAL,
        "canary_program": None,
        "autonomy": AUTONOMY_AUTH_BLOCKED_MANUAL,
        "notes": (
            "No official OAuth / public write API (llms.txt). "
            "AUTH_BLOCKED_MANUAL durable. No bypass."
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
        if _meta_telegram_live(meta or {})
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


def get_content_sync(platform: str) -> str | None:
    data = load_write_status()
    meta = (data.get("platforms") or {}).get(platform.strip().lower()) or {}
    raw = meta.get("content_sync")
    return str(raw) if raw else None


def is_sequence_cleared(platform: str) -> bool:
    """May later platforms proceed past this one?

    WRITE_VERIFIED always clears. SYNC_VERIFIED_NO_SAFE_DIFF also clears
    (honest no-diff, no fake write). Never implies Telegram live.
    """
    if is_write_verified(platform):
        return True
    return get_content_sync(platform) == CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF


def get_compare_class(platform: str) -> str | None:
    data = load_write_status()
    meta = (data.get("platforms") or {}).get(platform.strip().lower()) or {}
    raw = meta.get("last_compare_class")
    return str(raw) if raw else None


def record_compare_class(
    platform: str,
    cls: str,
    *,
    note: str | None = None,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record last compare class. Never promotes WRITE_VERIFIED."""
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    meta["last_compare_class"] = cls
    meta["last_compare_at"] = _now()
    if note:
        meta["last_compare_note"] = note
    if observed is not None:
        meta["last_compare_observed"] = observed
    save_write_status(data)
    return {
        "ok": True,
        "platform": platform,
        "last_compare_class": cls,
        "write_verified": meta.get("status") == STATUS_WRITE_VERIFIED,
    }


def is_blocked_compare(platform: str) -> bool:
    return get_compare_class(platform) in {COMPARE_AUTH_BLOCKED, COMPARE_DOM_BLOCKED}


def mark_sync_verified_no_safe_diff(
    platform: str,
    *,
    program: str = "kraken",
    notes: str | None = None,
) -> dict[str, Any]:
    """Record honest no-diff sync. Does not promote WRITE_VERIFIED."""
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    if meta.get("status") == STATUS_WRITE_VERIFIED:
        save_write_status(data)
        return {"ok": True, "status": STATUS_WRITE_VERIFIED, "content_sync": meta.get("content_sync")}
    if meta.get("status") not in {STATUS_CANARY_READY, STATUS_WRITE_PREPARED}:
        meta["status"] = STATUS_CANARY_READY
    meta["content_sync"] = CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF
    meta["canary_program"] = program
    meta["sync_verified_at"] = _now()
    if platform == "super-parrain":
        meta["runtime_mode"] = "CANARY_PENDING"
        meta["notes"] = notes or (
            "CANARY_READY + SYNC_VERIFIED_NO_SAFE_DIFF: Kraken already matches "
            "OPERATOR_VALIDATED. No fake write. Not WRITE_VERIFIED. "
            "Historical bumper remains SKIP. Later platforms may sequence."
        )
    elif notes:
        meta["notes"] = notes
    save_write_status(data)
    return {
        "ok": True,
        "platform": platform,
        "status": meta.get("status"),
        "content_sync": CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF,
        "write_verified": False,
    }


def get_platform_meta(platform: str) -> dict[str, Any]:
    data = load_write_status()
    return dict((data.get("platforms") or {}).get(platform.strip().lower()) or {})


def _meta_telegram_live(meta: dict[str, Any]) -> bool:
    """WRITE_VERIFIED is not enough: captcha/human save stays off Telegram live."""
    if (meta or {}).get("status") != STATUS_WRITE_VERIFIED:
        return False
    if (meta or {}).get("save_requires_captcha"):
        return False
    if (meta or {}).get("autonomy") == AUTONOMY_HUMAN_SAVE_REQUIRED:
        return False
    return True


def save_requires_human(platform: str) -> bool:
    meta = get_platform_meta(platform)
    return bool(
        meta.get("save_requires_captcha")
        or meta.get("autonomy") == AUTONOMY_HUMAN_SAVE_REQUIRED
    )


def autonomy_class(platform: str) -> str:
    meta = get_platform_meta(platform)
    raw = meta.get("autonomy")
    if raw:
        return str(raw)
    if meta.get("save_requires_captcha"):
        return AUTONOMY_HUMAN_SAVE_REQUIRED
    st = str(meta.get("status") or "")
    if st in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
        return AUTONOMY_AUTH_BLOCKED_MANUAL
    if st == STATUS_WRITE_VERIFIED:
        return AUTONOMY_FULLY_UNATTENDED
    return AUTONOMY_PC_OFF_READY if st == STATUS_CANARY_READY else "NOT_PROVEN"


def is_telegram_live_capable(platform: str) -> bool:
    """Unattended Telegram field updates only — not HUMAN_SAVE_REQUIRED."""
    return _meta_telegram_live(get_platform_meta(platform))


def is_canary_ready(platform: str) -> bool:
    return get_platform_status(platform) == STATUS_CANARY_READY


def telegram_action_for_platform(platform: str) -> str:
    """What Telegram path may do on this platform."""
    meta = get_platform_meta(platform)
    st = str(meta.get("status") or STATUS_UNPREPARED)
    if save_requires_human(platform):
        return AUTONOMY_HUMAN_SAVE_REQUIRED
    if st == STATUS_WRITE_VERIFIED:
        return "LIVE_UPDATE"
    if st == STATUS_CANARY_READY:
        return "CANARY_ONLY"  # explicit canary tool, not bulk telegram auto
    if st in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
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
    if platform == "super-parrain":
        meta["runtime_mode"] = "NORMAL_BUMP"
        meta["notes"] = (
            "WRITE_VERIFIED — content canary passed; historical bumper re-enabled."
        )
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
    from lib.phase import mark_write_verified as phase_mark, load_phase, save_phase

    phase_mark(platform)
    if platform == "super-parrain":
        try:
            ph = load_phase()
            ph["super_parrain_runtime"] = "NORMAL_BUMP"
            ph["note"] = (
                (ph.get("note") or "")
                + " Super-Parrain WRITE_VERIFIED: bumper re-enabled."
            ).strip()
            save_phase(ph)
        except Exception:
            pass
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
                "autonomy": autonomy_class(pid),
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
        if action == AUTONOMY_HUMAN_SAVE_REQUIRED:
            label = "HUMAN_SAVE_REQUIRED"
        elif st == STATUS_WRITE_VERIFIED and imp.get("status") == "pending_update":
            label = "UPDATED + VERIFIED" if action == "LIVE_UPDATE" else "PLAN (verified)"
        elif st == STATUS_WRITE_VERIFIED and imp.get("status") == "in_sync":
            label = "IN_SYNC + VERIFIED"
        elif st == STATUS_CANARY_READY:
            label = "CANARY_READY / PLAN_ONLY"
        elif st in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
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
