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
import os
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

# Autonomy classes (do not replace status). WRITE_VERIFIED ≠ unattended write.
AUTONOMY_FULLY_UNATTENDED = "FULLY_UNATTENDED"
AUTONOMY_PC_OFF_READY = "PC_OFF_READY"
AUTONOMY_HUMAN_SAVE_REQUIRED = "HUMAN_SAVE_REQUIRED"
AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN = "IMPORT_UI_BETA_NOT_PROVEN"
AUTONOMY_AUTH_BLOCKED_MANUAL = "AUTH_BLOCKED_MANUAL"
AUTONOMY_COOKIE_SESSION_NOT_PC_OFF = "COOKIE_SESSION_NOT_PC_OFF"
AUTONOMY_CANARY_PENDING_SKIP = "CANARY_PENDING_SKIP"
# super-parrain only: content-writer WRITE_VERIFIED, but the separate global
# historical bumper is on an explicit operator hold (fail-closed default —
# see lib.super_parrain_schedule.is_historical_bumper_authorized). Distinct
# from AUTONOMY_CANARY_PENDING_SKIP, which means the content-writer itself
# is not verified yet -- CANARY_PENDING_SKIP on an already-WRITE_VERIFIED
# platform is a stale/wrong label, not just an imprecise one.
AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED = "WRITE_VERIFIED_BUMPER_SUSPENDED"

# Runtime routes (Monitor → Hermes → writer). Human routes never auto-dispatch.
ROUTE_AUTO_ON_SAFE_DIFF = "AUTO_ON_SAFE_DIFF"
ROUTE_HUMAN_SAVE_REQUIRED = "HUMAN_SAVE_REQUIRED"
ROUTE_NEVER_AUTO_COMMIT = "NEVER_AUTO_COMMIT"
ROUTE_AUTH_BLOCKED_MANUAL = "AUTH_BLOCKED_MANUAL"
ROUTE_CANARY_PENDING_SKIP = "CANARY_PENDING_SKIP"
ROUTE_COOKIE_SESSION_NOT_PC_OFF = "COOKIE_SESSION_NOT_PC_OFF"
# super-parrain only: content-writer IS WRITE_VERIFIED but the separate
# global historical bumper has not been explicitly authorized (fail-closed).
# Distinct from ROUTE_CANARY_PENDING_SKIP (content-writer not verified yet)
# so operator-facing status never conflates the two independent gates.
ROUTE_BUMPER_NOT_AUTHORIZED = "BUMPER_NOT_AUTHORIZED"

HUMAN_LOCAL_COMMANDS = {
    "referralcode-tv": "python -u tools/local_headed_rctv_canary.py",
}

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
        "autonomy": AUTONOMY_CANARY_PENDING_SKIP,
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
        "autonomy": AUTONOMY_PC_OFF_READY,
        "notes": (
            "PC_OFF_READY: bumper.smart_login_parrainage + solve_turnstile. "
            "RM cookie optional. Auto only on a real SAFE_DIFF."
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


def _notify(level: str, event: str, **fields: Any) -> None:
    """BEST_EFFORT observability hook. Never affects status persistence."""
    try:
        from lib.notify import emit

        emit(level, event, **fields)
    except Exception:
        pass


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
        if _meta_auto_on_safe_diff(p, meta or {})
    ]
    data["write_verified_ratio"] = f"{data['write_verified_count']}/7"
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return STATUS_PATH


def ensure_write_status_file() -> dict[str, Any]:
    data = load_write_status()
    needs_save = not STATUS_PATH.exists()
    # merge defaults for missing platforms
    plats = data.setdefault("platforms", {})
    for pid, meta in DEFAULT_STATUS.items():
        if pid not in plats:
            plats[pid] = dict(meta)
            needs_save = True

    verified_count = sum(
        1
        for meta in plats.values()
        if (meta or {}).get("status") == STATUS_WRITE_VERIFIED
    )
    live_capable = [
        pid for pid, meta in plats.items() if _meta_auto_on_safe_diff(pid, meta or {})
    ]
    summary_fields = {
        "write_verified_count": verified_count,
        "telegram_live_capable": live_capable,
        "write_verified_ratio": f"{verified_count}/7",
    }
    for field, value in summary_fields.items():
        if data.get(field) != value:
            data[field] = value
            needs_save = True

    if needs_save:
        save_write_status(data)
        return load_write_status()
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


def _meta_auto_on_safe_diff(platform: str, meta: dict[str, Any]) -> bool:
    """Telegram live-capable requires BOTH: status == WRITE_VERIFIED AND
    autonomy == PC_OFF_READY. Super never auto.

    Real incident (2026-08-16): this previously checked autonomy alone.
    code-parrainage has had autonomy=PC_OFF_READY explicitly set since its
    own canary proved the writer mechanics (login/edit/reread), with its
    own notes documenting "Next real SAFE_DIFF is executable PC-off" as a
    deliberate design -- but it was still only status=CANARY_READY, never
    actually WRITE_VERIFIED (no real save has ever been proven there). That
    meant telegram_live_capable advertised it as Telegram-live-write-ready
    while data/platform-write-status.json's own top-level note says
    "Telegram live writes only for WRITE_VERIFIED platforms" -- a direct
    contradiction. autonomy=PC_OFF_READY describes the writer's *class*
    (no human/captcha step needed IF it runs) -- WRITE_VERIFIED status is
    the actual, current, per-platform proof that a real save+reread has
    succeeded. Both are required for Telegram to treat it as live-capable.
    """
    plat = (platform or "").strip().lower()
    if plat == "super-parrain":
        return False
    if (meta or {}).get("save_requires_captcha"):
        return False
    if (meta or {}).get("status") != STATUS_WRITE_VERIFIED:
        return False
    return (meta or {}).get("autonomy") == AUTONOMY_PC_OFF_READY


def save_requires_human(platform: str) -> bool:
    meta = get_platform_meta(platform)
    return bool(
        meta.get("save_requires_captcha")
        or meta.get("autonomy") == AUTONOMY_HUMAN_SAVE_REQUIRED
    )


def autonomy_class(platform: str) -> str:
    meta = get_platform_meta(platform)
    plat = platform.strip().lower()
    st = str(meta.get("status") or "")
    if plat == "super-parrain" and st == STATUS_WRITE_VERIFIED:
        # Fail-closed fallback: an unset autonomy field on an already
        # WRITE_VERIFIED super-parrain must never be read as bumper-authorized.
        return str(meta.get("autonomy") or AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED)
    raw = meta.get("autonomy")
    if raw:
        return str(raw)
    if meta.get("save_requires_captcha"):
        return AUTONOMY_HUMAN_SAVE_REQUIRED
    if st in {STATUS_AUTH_BLOCKED, STATUS_AUTH_BLOCKED_MANUAL}:
        return AUTONOMY_AUTH_BLOCKED_MANUAL
    if plat == "super-parrain":
        return AUTONOMY_CANARY_PENDING_SKIP
    if plat == "parrainage-co":
        return AUTONOMY_PC_OFF_READY
    if plat in {"1parrainage", "code-parrainage"}:
        return AUTONOMY_PC_OFF_READY
    return "NOT_PROVEN"


def runtime_route(platform: str) -> str:
    """Monitor → Hermes → writer dispatch class."""
    plat = (platform or "").strip().lower()
    if plat == "super-parrain":
        if not is_write_verified(plat):
            return ROUTE_CANARY_PENDING_SKIP
        # WRITE_VERIFIED alone never implies the global historical bumper is
        # authorized -- that is a separate, explicit, fail-closed gate (see
        # lib.super_parrain_schedule.is_historical_bumper_authorized).
        try:
            from lib.super_parrain_schedule import is_historical_bumper_authorized

            if is_historical_bumper_authorized():
                return "FUSED_UPDATE_BUMP"
        except Exception:
            pass
        return ROUTE_BUMPER_NOT_AUTHORIZED
    auto = autonomy_class(plat)
    if auto == AUTONOMY_PC_OFF_READY:
        return ROUTE_AUTO_ON_SAFE_DIFF
    if auto == AUTONOMY_HUMAN_SAVE_REQUIRED:
        return ROUTE_HUMAN_SAVE_REQUIRED
    if auto == AUTONOMY_IMPORT_UI_BETA_NOT_PROVEN:
        return ROUTE_NEVER_AUTO_COMMIT
    if auto in {AUTONOMY_AUTH_BLOCKED_MANUAL, STATUS_AUTH_BLOCKED}:
        return ROUTE_AUTH_BLOCKED_MANUAL
    if auto == AUTONOMY_COOKIE_SESSION_NOT_PC_OFF:
        return ROUTE_COOKIE_SESSION_NOT_PC_OFF
    return ROUTE_CANARY_PENDING_SKIP


def may_auto_execute_on_safe_diff(platform: str) -> bool:
    """True only for PC_OFF_READY writers that are ALSO currently
    WRITE_VERIFIED. Empty changed_fields stays NO_SAFE_DIFF.

    Real incident (2026-08-16): runtime_route()==ROUTE_AUTO_ON_SAFE_DIFF
    alone was previously treated as sufficient here. That route reflects a
    platform's writer *class* via autonomy_class() (autonomy=PC_OFF_READY,
    "no human/captcha step needed if it runs") -- not whether this specific
    platform has ever actually been proven WRITE_VERIFIED. code-parrainage
    had autonomy=PC_OFF_READY explicitly set once its writer mechanics were
    proven (login/edit/reread), with its own notes documenting "next real
    SAFE_DIFF is executable PC-off" as a deliberate design -- while still
    only CANARY_READY, no real save ever proven. Because this function is
    tools/run_verified_writers.py's _try_platform_if_verified()'s ONLY
    gate before calling execute(plan, dry_run=False), that meant a real
    detected SAFE_DIFF could have triggered a genuine unattended live write
    to a platform that had never been write-verified. Both conditions are
    now required; runtime_route()'s own return value is intentionally left
    unchanged (still useful as an informational "writer class" signal for
    routing/status displays).
    """
    if runtime_route(platform) != ROUTE_AUTO_ON_SAFE_DIFF:
        return False
    return get_platform_status(platform) == STATUS_WRITE_VERIFIED


def human_local_command(platform: str) -> str | None:
    if runtime_route(platform) != ROUTE_HUMAN_SAVE_REQUIRED:
        return None
    return HUMAN_LOCAL_COMMANDS.get(
        platform.strip().lower(),
        "headed local save required — no GH auto-save",
    )


def is_telegram_live_capable(platform: str) -> bool:
    """Alias: may auto-dispatch writer on a real SAFE_DIFF. Not proven unattended."""
    return may_auto_execute_on_safe_diff(platform)


def is_canary_ready(platform: str) -> bool:
    return get_platform_status(platform) == STATUS_CANARY_READY


def telegram_action_for_platform(platform: str) -> str:
    """What Hermes/Telegram may do on this platform."""
    return runtime_route(platform)


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
    bumper_authorized = False
    if platform == "super-parrain":
        # WRITE_VERIFIED proves this one targeted content-writer canary
        # works. It must NEVER by itself authorize the separate, much
        # larger-blast-radius global historical bumper (~35 programs, 1
        # Enregistrer/code) -- that requires an explicit, independent
        # operator authorize_historical_bumper() call. Fail-closed: read
        # the *actual* authorization flag rather than assuming NORMAL_BUMP.
        try:
            from lib.super_parrain_schedule import is_historical_bumper_authorized

            bumper_authorized = is_historical_bumper_authorized()
        except Exception:
            bumper_authorized = False
        meta["runtime_mode"] = "NORMAL_BUMP" if bumper_authorized else "WRITE_VERIFIED_BUMPER_SUSPENDED"
        meta["autonomy"] = (
            "FUSED_UPDATE_BUMP" if bumper_authorized else AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED
        )
        meta["notes"] = (
            "WRITE_VERIFIED — content-writer proof (login+edit+save+reread+post_match). "
            + (
                "Historical bumper explicitly authorized — NORMAL_BUMP."
                if bumper_authorized
                else (
                    "Historical bumper NOT authorized (fail-closed, independent from "
                    "this content-writer proof) — bump_super_parrain.yml stays SKIP "
                    "until an operator calls "
                    "lib.super_parrain_schedule.authorize_historical_bumper()."
                )
            )
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
            ph["super_parrain_runtime"] = meta["runtime_mode"]
            ph["note"] = (
                (ph.get("note") or "")
                + (
                    " Super-Parrain content-writer WRITE_VERIFIED; historical "
                    + (
                        "bumper authorized — NORMAL_BUMP."
                        if bumper_authorized
                        else "bumper NOT authorized (fail-closed) — stays SUSPENDED."
                    )
                )
            ).strip()
            save_phase(ph)
        except Exception:
            pass
    _notify(
        "SUCCESS",
        "platform_status_change",
        platform=platform,
        program=program,
        field="status",
        new_value=STATUS_WRITE_VERIFIED,
        action="mark_write_verified",
        result="WRITE_VERIFIED",
        post_match=bool(evidence.get("post_match")),
        immutable=bool(evidence.get("immutable_ok", checks.get("immutable_preserved"))),
        pc_required=save_requires_human(platform),
        source=str(evidence.get("source") or "canary"),
    )
    return {"ok": True, "platform": platform, "status": STATUS_WRITE_VERIFIED}


def record_operational_cycle(
    platform: str,
    *,
    result: str,
    post_match: bool | None = None,
    program: str | None = None,
    gh_run_id: str | None = None,
    saves: str | None = None,
    error: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record what the LATEST real cycle actually did.

    Deliberately separate from the historical proof. This never touches
    ``status``, ``evidence`` or ``last_write_verified_at``: WRITE_VERIFIED is a
    durable statement about what the writer has been proven capable of, and a
    later failure does not retract that proof.

    What it does fix is the opposite error, seen after GH run 33098049116: the
    cycle failed post-verify, yet ``last_operational_cycle`` still advertised the
    2026-08-20 PASS, so the record implied the most recent cycle had succeeded.
    """
    data = load_write_status()
    meta = data.setdefault("platforms", {}).setdefault(platform, {})
    entry: dict[str, Any] = {
        "at": _now(),
        "result": result,
        "post_verify": "PASS" if post_match is True else ("FAIL" if post_match is False else "NONE"),
        "post_match": post_match,
        "program": program,
        "gh_run_id": gh_run_id or os.environ.get("GITHUB_RUN_ID"),
        "saves": saves,
    }
    if error:
        entry["error"] = error
    entry["note"] = note or (
        "Latest actual cycle. Independent from evidence/last_write_verified_at, "
        "which remain the historical capability proof and are not retracted here."
    )
    meta["last_operational_cycle"] = {k: v for k, v in entry.items() if v is not None}
    save_write_status(data)
    return meta["last_operational_cycle"]


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
    _notify(
        "ERROR",
        "post_verify_failure",
        platform=platform,
        program=program,
        action="mark_canary_failed",
        result="FAILED",
        block_reason=error,
        source="lib.write_status",
    )


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
                "route": runtime_route(pid),
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
        if action == ROUTE_HUMAN_SAVE_REQUIRED:
            label = "HUMAN_SAVE_REQUIRED"
            cmd = human_local_command(pid)
            if cmd and ch:
                extra_cmd = f" | {cmd}"
            else:
                extra_cmd = ""
        elif action == ROUTE_AUTO_ON_SAFE_DIFF and imp.get("status") == "pending_update":
            label = "AUTO_ON_SAFE_DIFF"
            extra_cmd = ""
        elif action == ROUTE_AUTO_ON_SAFE_DIFF:
            label = "PC_OFF_READY"
            extra_cmd = ""
        elif action == ROUTE_NEVER_AUTO_COMMIT:
            label = "NEVER_AUTO_COMMIT"
            extra_cmd = ""
        elif action == ROUTE_CANARY_PENDING_SKIP:
            label = "SKIP"
            extra_cmd = ""
        elif action == ROUTE_BUMPER_NOT_AUTHORIZED:
            label = "WRITE_VERIFIED_BUMPER_SUSPENDED"
            extra_cmd = ""
        elif action == ROUTE_COOKIE_SESSION_NOT_PC_OFF:
            label = "COOKIE_SESSION"
            extra_cmd = ""
        elif action == ROUTE_AUTH_BLOCKED_MANUAL or st in {
            STATUS_AUTH_BLOCKED,
            STATUS_AUTH_BLOCKED_MANUAL,
        }:
            label = "AUTH_BLOCKED"
            extra_cmd = ""
        elif st == STATUS_WRITE_PREPARED:
            label = "PLAN_ONLY"
            extra_cmd = ""
        else:
            label = st
            extra_cmd = ""
        extra = ""
        if ch:
            extra = " " + ",".join(list(ch.keys())[:3])
        lines.append(f"  {pid:18} {label}{extra}{extra_cmd}")
    return lines
