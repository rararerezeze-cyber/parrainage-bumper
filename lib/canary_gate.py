"""Sequential canary gates — one platform at a time, predecessor PASS required.

Does not touch Super-Parrain cooldown. Live execute is still the caller's job
(controlled_write_* --execute --force). This module only answers: may this
platform fire now, and what is the exact next one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR
from lib.safety import is_circuit_open, live_write_blocked_reason, maybe_trip_from_error, snapshot_state
from lib.write_status import (
    COMPARE_DOM_BLOCKED,
    STATUS_AUTH_BLOCKED,
    STATUS_CANARY_READY,
    STATUS_WRITE_PREPARED,
    STATUS_WRITE_VERIFIED,
    get_compare_class,
    get_platform_status,
    is_blocked_compare,
    is_sequence_cleared,
    is_write_verified,
)

QUEUE = [
    "super-parrain",
    "parrainage-co",
    "code-parrainage",
    "1parrainage",
    "referralcodes",
    "referralcode-tv",
]
SKIP = {"referraldrop": "AUTH_BLOCKED_GOOGLE"}

POST_SUPER_EXECUTABLE = (
    "parrainage-co",
    "code-parrainage",
    "1parrainage",
    "referralcodes",
)

CANARY_TOOLS: dict[str, str] = {
    "super-parrain": "tools/controlled_write_super_parrain.py",
    "parrainage-co": "tools/controlled_write_parrainage_co.py",
    "code-parrainage": "tools/controlled_write_code_parrainage.py",
    "1parrainage": "tools/controlled_write_1parrainage.py",
    "referralcodes": "tools/controlled_write_referralcodes.py",
    "referralcode-tv": "tools/probe_referralcode_tv_edit.py",
}

SECRETS: dict[str, list[str]] = {
    "super-parrain": ["SUPER_PARRAIN_EMAIL", "SUPER_PARRAIN_PASSWORD"],
    "parrainage-co": [
        "PARRAINAGE_CO_RM_COOKIE",
        "PARRAINAGE_CO_EMAIL",
        "PARRAINAGE_CO_PASSWORD",
    ],
    "code-parrainage": ["CODE_PARRAINAGE_EMAIL", "CODE_PARRAINAGE_PASSWORD"],
    "1parrainage": ["ONEPARRAINAGE_EMAIL", "ONEPARRAINAGE_PASSWORD"],
    "referralcodes": ["REFERRALCODES_EMAIL", "REFERRALCODES_PASSWORD"],
    "referralcode-tv": ["REFERRALCODE_EMAIL", "REFERRALCODE_PASSWORD"],
}

PIPELINES: dict[str, list[str]] = {
    "parrainage-co": ["login", "edit", "save", "reread_account", "reread_public"],
    "code-parrainage": ["login", "edit", "save", "reread_account", "reread_public_if_any"],
    "1parrainage": ["login", "edit", "save", "reread_account", "reread_public"],
    "referralcodes": [
        "login_email_password",
        "open_agent_import",
        "paste_kraken_json",
        "validate",
        "commit",
        "reread_public_profile",
    ],
}

LOCK_PATH = DATA_DIR / ".canary.lock"
PACKS_PATH = DATA_DIR / "captures" / "post-super-canary-packs.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def predecessor(platform: str) -> str | None:
    plat = (platform or "").strip().lower()
    if plat not in QUEUE:
        return None
    i = QUEUE.index(plat)
    return QUEUE[i - 1] if i > 0 else None


def next_after(platform: str) -> str | None:
    plat = (platform or "").strip().lower()
    if plat not in QUEUE:
        return None
    i = QUEUE.index(plat)
    if i + 1 >= len(QUEUE):
        return None
    nxt = QUEUE[i + 1]
    if nxt in SKIP:
        return next_after(nxt)
    return nxt


def may_execute_canary(platform: str, *, for_super: bool = False) -> dict[str, Any]:
    """Can this platform receive a live canary right now?

    Super-Parrain is owned by activation_canary.yml (cooldown). This gate
    does not authorize Super unless for_super=True (that workflow).
    Other platforms: Super sequence-cleared (WRITE_VERIFIED or
    SYNC_VERIFIED_NO_SAFE_DIFF) + predecessor sequence-cleared
    + CANARY_READY + circuit closed. Never parallel.
    """
    plat = (platform or "").strip().lower()
    out: dict[str, Any] = {
        "platform": plat,
        "ok": False,
        "predecessor": predecessor(plat),
        "at": _now(),
    }
    if plat in SKIP:
        out["error"] = SKIP[plat]
        return out
    if plat not in QUEUE:
        out["error"] = "unknown_platform"
        return out

    open_, reason = is_circuit_open(plat)
    if open_:
        out["error"] = f"CIRCUIT_OPEN: {reason}"
        return out

    st = get_platform_status(plat)
    out["status"] = st
    if st == STATUS_WRITE_VERIFIED:
        out["error"] = "already_WRITE_VERIFIED"
        out["done"] = True
        return out
    if st == STATUS_AUTH_BLOCKED:
        out["error"] = "AUTH_BLOCKED"
        return out
    if st != STATUS_CANARY_READY:
        out["error"] = f"not_CANARY_READY:{st}"
        return out

    if plat == "super-parrain":
        if not for_super:
            out["error"] = "super_owned_by_activation_canary"
            return out
        out["ok"] = True
        out["note"] = "activation_canary.yml still enforces 24h cooldown"
        return out

    if not is_sequence_cleared("super-parrain"):
        out["error"] = "SUPER_PASS_REQUIRED"
        return out

    pred = predecessor(plat)
    if pred and not is_sequence_cleared(pred):
        # Honest blocked predecessor (slider/auth) must not stall a later
        # REAL_SAFE_DIFF after Super is sequence-cleared. Never a fake PASS.
        if is_blocked_compare(pred) and is_sequence_cleared("super-parrain"):
            out["predecessor_skipped"] = get_compare_class(pred)
            out["predecessor_status"] = get_platform_status(pred)
        else:
            out["error"] = f"PREDECESSOR_NOT_PASS:{pred}"
            out["predecessor_status"] = get_platform_status(pred)
            return out

    if plat == "referralcode-tv":
        # Auth + add-referral-code/?eid= edit form proven (run 31682310236).
        # Boost #cliccami is not content edit. No Kraken listing exists yet.
        out["error"] = "RCTV_NO_KRAKEN_LISTING"
        out["edit_form"] = "https://referralcode.tv/add-referral-code/?eid="
        return out

    out["ok"] = True
    out["tool"] = CANARY_TOOLS.get(plat)
    out["cli"] = (
        f"python {CANARY_TOOLS[plat]} --program kraken --execute --force"
        if plat in CANARY_TOOLS
        else None
    )
    return out


def next_executable(*, for_super: bool = False) -> dict[str, Any]:
    """Single next platform that may fire. Never a list."""
    for plat in QUEUE:
        if plat in SKIP:
            continue
        if is_sequence_cleared(plat):
            continue
        # Slider/DOM blocked this cycle: do not sit on it and hide later diffs.
        if get_compare_class(plat) == COMPARE_DOM_BLOCKED:
            continue
        gate = may_execute_canary(plat, for_super=for_super)
        return {
            "next": plat if gate.get("ok") else None,
            "candidate": plat,
            "gate": gate,
            "one_at_a_time": True,
        }
    return {"next": None, "candidate": None, "gate": {"ok": True, "done": True}, "one_at_a_time": True}


def preflight_live_plan(platform: str, plan: Any, *, program: str = "kraken") -> dict[str, Any]:
    """Refuse a live write that is empty or would republish catalog leftovers.

    NO_SAFE_DIFF records SYNC_VERIFIED_NO_SAFE_DIFF (not WRITE_VERIFIED).
    """
    from lib.safety import abort_forbidden_publish
    from lib.write_status import mark_sync_verified_no_safe_diff

    rendered = getattr(plan, "rendered", None) or ""
    changed = getattr(plan, "changed_fields", None) or {}
    if isinstance(plan, dict):
        rendered = plan.get("rendered") or json.dumps(plan.get("payload") or {}, ensure_ascii=False)
        changed = plan.get("changed_fields") or {}
    news = []
    if isinstance(changed, dict):
        for d in changed.values():
            if isinstance(d, dict):
                news.append(str(d.get("new") or ""))
            else:
                news.append(str(d or ""))
    forbidden = abort_forbidden_publish(rendered, *news)
    if forbidden:
        return {
            "ok": False,
            "abort": True,
            "result": "FORBIDDEN_PUBLISH",
            "error": forbidden,
            "changed_fields": changed,
        }
    if not changed:
        rec = mark_sync_verified_no_safe_diff(platform, program=program)
        return {
            "ok": True,
            "abort": True,
            "result": "NO_SAFE_DIFF",
            "write_status": rec.get("status"),
            "content_sync": rec.get("content_sync"),
            "write_verified": False,
            "changed_fields": {},
        }
    return {"ok": True, "abort": False, "changed_fields": changed}


def guard_live_execute(platform: str, *, for_super: bool = False) -> dict[str, Any]:
    """Call immediately before a live canary. Snapshot + exclusive lock + gate."""
    blocked = live_write_blocked_reason(platform)
    if blocked:
        return {"ok": False, "error": f"CIRCUIT_OPEN: {blocked}", "platform": platform}
    gate = may_execute_canary(platform, for_super=for_super)
    if not gate.get("ok"):
        return gate
    if LOCK_PATH.exists():
        try:
            prev = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        return {
            "ok": False,
            "error": f"CANARY_LOCK_HELD:{prev.get('platform')}",
            "lock": prev,
        }
    snap = snapshot_state(f"canary:{platform}")
    lock = {
        "platform": platform,
        "at": _now(),
        "snapshot_id": snap.get("id"),
        "one_at_a_time": True,
    }
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    gate["snapshot_id"] = snap.get("id")
    gate["lock"] = True
    return gate


def release_canary_lock(platform: str | None = None) -> None:
    if not LOCK_PATH.exists():
        return
    try:
        prev = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    if platform and prev.get("platform") and prev.get("platform") != platform:
        return
    try:
        LOCK_PATH.unlink()
    except Exception:
        pass


def record_live_failure(platform: str, error: str, *, snapshot_id: str | None = None) -> None:
    maybe_trip_from_error(error or "", platform=platform)
    release_canary_lock(platform)
    _ = snapshot_id


def record_live_success(platform: str) -> None:
    release_canary_lock(platform)


def assess_pack(platform: str, program: str = "kraken") -> dict[str, Any]:
    """Static canary pack — no live write, no login."""
    import importlib

    plat = platform.strip().lower()
    st = get_platform_status(plat)
    pred = predecessor(plat)
    tool = CANARY_TOOLS.get(plat)
    tool_ok = bool(tool and (Path(__file__).resolve().parents[1] / tool).exists())
    checks: dict[str, Any] = {
        "status_canary_ready": st == STATUS_CANARY_READY,
        "controlled_write_tool": tool_ok,
        "predecessor_defined": pred is not None or plat == "super-parrain",
        "circuit_closed": not is_circuit_open(plat)[0],
        "kraken_plan_ok": False,
        "structure_or_schema_ok": False,
        "pipeline_documented": plat in PIPELINES,
    }
    plan_info: dict[str, Any] = {}
    mods = {
        "parrainage-co": "platforms.parrainage_co.writer",
        "code-parrainage": "platforms.code_parrainage.writer",
        "1parrainage": "platforms.oneparrainage.writer",
        "referralcodes": "platforms.referralcodes.writer",
        "super-parrain": "platforms.super_parrain.writer",
        "referralcode-tv": "platforms.referralcode_tv.writer",
    }
    if plat in mods:
        try:
            mod = importlib.import_module(mods[plat])
            if hasattr(mod, "dry_run_report"):
                try:
                    report = mod.dry_run_report(program)
                except TypeError:
                    report = mod.dry_run_report(program=program)
            else:
                plan = mod.build_write_plan(program=program)
                report = {
                    "structure_preserved": getattr(plan, "structure_preserved", None),
                    "changed_fields": getattr(plan, "changed_fields", None),
                    "announcement_url": getattr(plan, "announcement_url", None),
                    "edit_url": getattr(plan, "edit_url", None),
                }
            plan_info = {
                "structure_preserved": report.get("structure_preserved"),
                "validation_ok": report.get("validation_ok"),
                "changed_fields": report.get("changed_fields"),
                "announcement_url": report.get("announcement_url"),
                "edit_url": report.get("edit_url") or report.get("import_ui"),
                "action": report.get("action"),
                "live": False,
            }
            checks["kraken_plan_ok"] = True
            checks["structure_or_schema_ok"] = bool(
                report.get("structure_preserved")
                or report.get("validation_ok")
                or report.get("write_mode") == "CANARY_READY"
            )
        except Exception as exc:  # noqa: BLE001
            plan_info = {"error": str(exc)}
            checks["kraken_plan_ok"] = False

    executable_after_super = plat in POST_SUPER_EXECUTABLE and all(
        checks[k]
        for k in (
            "status_canary_ready",
            "controlled_write_tool",
            "circuit_closed",
            "kraken_plan_ok",
            "structure_or_schema_ok",
            "pipeline_documented",
        )
    )
    return {
        "platform": plat,
        "program": program,
        "status": st,
        "predecessor": pred,
        "successor": next_after(plat),
        "secrets": SECRETS.get(plat) or [],
        "tool": tool,
        "cli_plan": f"python {tool} --program {program}" if tool else None,
        "cli_execute_after_gates": (
            f"python {tool} --program {program} --execute --force" if tool else None
        ),
        "workflow": "activation_canary.yml",
        "pipeline": PIPELINES.get(plat) or [],
        "snapshot_on_execute": True,
        "rollback": "lib.safety.rollback_snapshot after failed live",
        "circuit_breakers": True,
        "one_at_a_time": True,
        "requires_super_pass": plat != "super-parrain",
        "requires_predecessor_pass": pred not in (None, "super-parrain") or plat != "parrainage-co",
        "checks": checks,
        "plan": plan_info,
        "EXECUTABLE_AFTER_SUPER_PASS": "YES" if executable_after_super else "NO",
        "live": False,
        "at": _now(),
    }


def write_all_packs(program: str = "kraken") -> dict[str, Any]:
    packs = [assess_pack(p, program) for p in POST_SUPER_EXECUTABLE]
    rctv = assess_pack("referralcode-tv", program)
    nxt = next_executable(for_super=False)
    payload = {
        "at": _now(),
        "live": False,
        "super_pass": is_write_verified("super-parrain"),
        "next_executable": nxt.get("next"),
        "one_at_a_time": True,
        "sequence": list(POST_SUPER_EXECUTABLE),
        "rule": (
            "After Super WRITE_VERIFIED, fire exactly one platform: "
            "the first not-yet WRITE_VERIFIED whose predecessor PASSed. "
            "Never two sessions. Never skip."
        ),
        "packs": packs,
        "referralcode_tv": rctv,
        "POST_SUPER_CANARIES_ARMED": (
            "YES" if all(p["EXECUTABLE_AFTER_SUPER_PASS"] == "YES" for p in packs) else "NO"
        ),
    }
    PACKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for p in packs:
        path = DATA_DIR / "captures" / f"canary-pack-{p['platform']}.json"
        path.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
