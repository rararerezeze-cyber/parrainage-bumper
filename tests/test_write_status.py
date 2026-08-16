"""Strict WRITE_VERIFIED semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import write_status as ws


@pytest.fixture()
def status_path(tmp_path, monkeypatch):
    p = tmp_path / "platform-write-status.json"
    monkeypatch.setattr(ws, "STATUS_PATH", p)
    # fresh defaults
    data = {
        "version": 1,
        "platforms": {k: dict(v) for k, v in ws.DEFAULT_STATUS.items()},
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_super_parrain_is_canary_ready_not_verified(status_path):
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY
    assert ws.is_write_verified("super-parrain") is False
    assert ws.is_telegram_live_capable("super-parrain") is False
    assert ws.telegram_action_for_platform("super-parrain") == ws.ROUTE_CANARY_PENDING_SKIP
    assert ws.may_auto_execute_on_safe_diff("super-parrain") is False


def test_canary_ready_with_pc_off_ready_autonomy_is_not_telegram_live_capable(status_path):
    """Regression for a real incident (2026-08-16): telegram_live_capable
    previously required only autonomy == PC_OFF_READY, so code-parrainage
    (status=CANARY_READY, autonomy=PC_OFF_READY -- a real, deliberately-set
    combination once its writer mechanics were proven, documented as "next
    real SAFE_DIFF is executable PC-off") was advertised as Telegram
    live-write capable despite never having been WRITE_VERIFIED -- directly
    contradicting the status file's own top-level note ("Telegram live
    writes only for WRITE_VERIFIED platforms"). Both conditions are now
    required.
    """
    assert ws.get_platform_status("code-parrainage") == ws.STATUS_CANARY_READY
    meta = ws.get_platform_meta("code-parrainage")
    assert meta.get("autonomy") == ws.AUTONOMY_PC_OFF_READY  # the trap: looks auto-capable
    assert ws.is_telegram_live_capable("code-parrainage") is False
    assert "code-parrainage" not in ws.summary()["telegram_live_capable"]


def test_promoting_that_same_platform_to_write_verified_makes_it_live_capable(
    status_path, monkeypatch, tmp_path
):
    """The flip side: once code-parrainage is genuinely WRITE_VERIFIED
    (full evidence, same as any other platform), it correctly becomes
    Telegram live-capable -- this isn't a blanket exclusion of
    code-parrainage, only of an unearned CANARY_READY status.
    """
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)

    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    r = ws.mark_write_verified(
        "code-parrainage",
        program="kraken",
        evidence={"post_match": True, "checks": checks},
    )
    assert r["ok"] is True
    assert ws.is_telegram_live_capable("code-parrainage") is True
    assert "code-parrainage" in ws.summary()["telegram_live_capable"]


def test_cannot_mark_verified_without_evidence(status_path):
    r = ws.mark_write_verified(
        "super-parrain",
        program="kraken",
        evidence={"post_match": True, "checks": {"authenticated": True}},
    )
    assert r["ok"] is False
    assert "missing_checks" in r
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY


def test_mark_verified_with_full_evidence(status_path, monkeypatch, tmp_path):
    # avoid mutating real phase file
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)

    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    r = ws.mark_write_verified(
        "super-parrain",
        program="kraken",
        evidence={
            "post_match": True,
            "checks": checks,
            "announcement_url": "https://example.test/a",
            "edit_url": "https://example.test/e",
            "public_reread": True,
            "immutable_ok": True,
        },
    )
    assert r["ok"] is True
    assert ws.is_write_verified("super-parrain")
    # Bumper not explicitly authorized (fail-closed default) -> not FUSED_UPDATE_BUMP
    # yet, even though the content-writer is WRITE_VERIFIED.
    assert ws.runtime_route("super-parrain") == ws.ROUTE_BUMPER_NOT_AUTHORIZED
    assert ws.may_auto_execute_on_safe_diff("super-parrain") is False
    assert ws.is_telegram_live_capable("super-parrain") is False
    s = ws.summary()
    assert s["write_verified_count"] == 1
    assert "super-parrain" not in s["telegram_live_capable"]
    assert s["WRITE_VERIFIED"] == "1/7"

    from lib.super_parrain_schedule import authorize_historical_bumper

    authorize_historical_bumper("test operator sign-off", actor="test")
    assert ws.runtime_route("super-parrain") == "FUSED_UPDATE_BUMP"
    assert ws.may_auto_execute_on_safe_diff("super-parrain") is False


def test_referraldrop_auth_blocked(status_path):
    assert ws.get_platform_status("referraldrop") == ws.STATUS_AUTH_BLOCKED_MANUAL
    assert ws.telegram_action_for_platform("referraldrop") == ws.ROUTE_AUTH_BLOCKED_MANUAL
    assert ws.autonomy_class("referraldrop") == ws.AUTONOMY_AUTH_BLOCKED_MANUAL


def test_rctv_write_verified_is_not_telegram_live(status_path, monkeypatch, tmp_path):
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)
    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    r = ws.mark_write_verified(
        "referralcode-tv",
        program="kraken",
        evidence={"post_match": True, "checks": checks},
    )
    assert r["ok"] is True
    data = ws.load_write_status()
    data["platforms"]["referralcode-tv"]["save_requires_captcha"] = True
    data["platforms"]["referralcode-tv"]["autonomy"] = ws.AUTONOMY_HUMAN_SAVE_REQUIRED
    ws.save_write_status(data)
    assert ws.is_write_verified("referralcode-tv")
    assert ws.is_telegram_live_capable("referralcode-tv") is False
    assert ws.telegram_action_for_platform("referralcode-tv") == ws.ROUTE_HUMAN_SAVE_REQUIRED
    assert "referralcode-tv" not in ws.summary()["telegram_live_capable"]
    assert ws.may_auto_execute_on_safe_diff("referralcode-tv") is False
    assert ws.human_local_command("referralcode-tv") == "python -u tools/local_headed_rctv_canary.py"


def test_count_starts_at_zero(status_path):
    s = ws.summary()
    assert s["write_verified_count"] == 0
    assert s["WRITE_VERIFIED"] == "0/7"


def test_sync_verified_no_safe_diff_is_not_write_verified(status_path):
    r = ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    assert r["ok"] is True
    assert r["write_verified"] is False
    assert ws.is_write_verified("super-parrain") is False
    assert ws.is_telegram_live_capable("super-parrain") is False
    assert ws.is_sequence_cleared("super-parrain") is True
    assert ws.get_content_sync("super-parrain") == ws.CONTENT_SYNC_VERIFIED_NO_SAFE_DIFF
    assert ws.get_platform_status("super-parrain") == ws.STATUS_CANARY_READY


def test_compare_class_does_not_write_verify(status_path):
    r = ws.record_compare_class(
        "code-parrainage", ws.COMPARE_DOM_BLOCKED, note="slider"
    )
    assert r["write_verified"] is False
    assert ws.get_compare_class("code-parrainage") == ws.COMPARE_DOM_BLOCKED
    assert ws.is_blocked_compare("code-parrainage") is True
    assert ws.is_write_verified("code-parrainage") is False
    assert ws.is_sequence_cleared("code-parrainage") is False


def test_write_verified_never_downgraded_by_later_no_safe_diff(status_path, monkeypatch, tmp_path):
    """A proven save+reread+post_match must survive a later NO_SAFE_DIFF.

    Regression: on 2026-08-13 controlled_write_super_parrain.py genuinely
    promoted super-parrain to WRITE_VERIFIED for poulpeo (real login, fill,
    save, reread_account, reread_public, post_match=true — see
    data/captures/write-super-parrain-poulpeo.json), but the promotion never
    reached git because controlled_write.yml's commit step didn't stage
    platform-write-status.json. Every later canary run then re-read a stale
    CANARY_READY status from git and recorded NO_SAFE_DIFF for the kraken
    canary program, which looked like WRITE_VERIFIED had been erased. The
    persistence gap is fixed in controlled_write.yml; this test locks the
    invariant at the logic layer so it can never regress silently again:
    once WRITE_VERIFIED, a NO_SAFE_DIFF on any program must never downgrade
    the platform's status, only WRITE_VERIFIED itself may re-promote it.
    """
    phase = tmp_path / "phase.json"
    phase.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase)

    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    r = ws.mark_write_verified(
        "super-parrain",
        program="poulpeo",
        evidence={"post_match": True, "checks": checks},
    )
    assert r["ok"] is True
    assert ws.get_platform_status("super-parrain") == ws.STATUS_WRITE_VERIFIED
    # WRITE_VERIFIED alone never authorizes the separate global historical
    # bumper (fail-closed by design — see
    # tests/test_super_parrain_bumper_authorization.py) — runtime_mode
    # starts SUSPENDED, not NORMAL_BUMP, until an operator explicitly
    # authorizes the bumper.
    assert (
        ws.load_write_status()["platforms"]["super-parrain"]["runtime_mode"]
        == "WRITE_VERIFIED_BUMPER_SUSPENDED"
    )

    # A later canary on the designated gating program (kraken) that finds
    # nothing to change must record NO_SAFE_DIFF without ever rolling the
    # platform back below WRITE_VERIFIED.
    r2 = ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    assert r2["ok"] is True
    assert r2["status"] == ws.STATUS_WRITE_VERIFIED
    assert ws.get_platform_status("super-parrain") == ws.STATUS_WRITE_VERIFIED
    assert ws.is_write_verified("super-parrain") is True
    assert (
        ws.load_write_status()["platforms"]["super-parrain"]["runtime_mode"]
        == "WRITE_VERIFIED_BUMPER_SUSPENDED"
    )
    assert (
        ws.load_write_status()["platforms"]["super-parrain"].get("last_write_verified_at")
        is not None
    )


def test_dom_blocked_predecessor_does_not_stall_later_real_diff(status_path):
    from lib import canary_gate as cg

    ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    ws.mark_sync_verified_no_safe_diff("parrainage-co", program="kraken")
    ws.record_compare_class("code-parrainage", ws.COMPARE_DOM_BLOCKED, note="slider")
    nxt = cg.next_executable()
    assert nxt["next"] == "1parrainage"
    gate = cg.may_execute_canary("1parrainage")
    assert gate.get("ok") is True
    assert gate.get("predecessor_skipped") == ws.COMPARE_DOM_BLOCKED
    assert ws.is_write_verified("1parrainage") is False
