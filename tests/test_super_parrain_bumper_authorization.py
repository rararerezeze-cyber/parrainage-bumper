"""Historical bumper authorization is independent from WRITE_VERIFIED.

WRITE_VERIFIED proves ONE targeted content-writer canary (login, edit, save,
reread_account, reread_public, post_match=true -- the real Poulpeo canary,
data/captures/write-super-parrain-poulpeo.json). The global historical
bumper (~35 super-parrain programs, 1 Enregistrer/code each) is a much
larger blast radius and must stay fail-closed until an operator explicitly
authorizes it, independent of WRITE_VERIFIED.

Regression this guards against: lib.write_status.mark_write_verified() used
to hardcode runtime_mode="NORMAL_BUMP" for super-parrain the moment
WRITE_VERIFIED became true, and activation_canary.yml asserted mode ==
"NORMAL_BUMP" right after a verified write -- both wrongly treated
"content-writer proof" and "global bumper allowed to run" as the same
event.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib import super_parrain_schedule as sps
from lib import write_status as ws

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    status_p = tmp_path / "platform-write-status.json"
    status_p.write_text(
        json.dumps({"version": 1, "platforms": {k: dict(v) for k, v in ws.DEFAULT_STATUS.items()}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ws, "STATUS_PATH", status_p)

    phase_p = tmp_path / "autofresh-phase.json"
    phase_p.write_text(
        json.dumps({"phase": "VALIDATION_LIVE", "live_writes": True, "write_verified": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase_p)

    # No last_super_run.txt written yet -> cooldown wide open (eligible=True)
    # by design, so these tests isolate the authorization gate itself from
    # the (unrelated, already-tested) cooldown gate.
    monkeypatch.setattr(sps, "LAST_SUPER_RUN", tmp_path / "last_super_run.txt")
    monkeypatch.setattr(sps, "PENDING_PATH", tmp_path / "pending_writes.json")

    return {"status": status_p, "phase": phase_p}


def _mark_super_parrain_write_verified(program: str = "poulpeo") -> dict:
    checks = {k: True for k in ws.REQUIRED_VERIFY_CHECKS}
    return ws.mark_write_verified(
        "super-parrain",
        program=program,
        evidence={"post_match": True, "checks": checks},
    )


def test_write_verified_alone_does_not_authorize_bumper(isolated_state):
    r = _mark_super_parrain_write_verified()
    assert r["ok"] is True
    assert ws.is_write_verified("super-parrain") is True
    assert sps.is_historical_bumper_authorized() is False
    assert sps.super_parrain_runtime_mode() == sps.RUNTIME_MODE_BUMPER_SUSPENDED
    # autonomy must say "verified but bumper on hold", never something that
    # reads as "content-writer not verified yet" (that's a different, wrong
    # state -- AUTONOMY_CANARY_PENDING_SKIP) nor "bumper is running"
    # (AUTONOMY_FUSED_UPDATE_BUMP would be, before authorization).
    meta = ws.load_write_status()["platforms"]["super-parrain"]
    assert meta["autonomy"] == ws.AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED
    assert ws.autonomy_class("super-parrain") == ws.AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED


def test_write_verified_plus_unauthorized_bumper_means_skip_even_when_eligible(isolated_state):
    """WRITE_VERIFIED + bumper non autorisé => SKIP, even with cooldown open."""
    _mark_super_parrain_write_verified()
    decision = sps.decide_super_parrain_action()
    assert decision["action"] == "skip"
    assert decision["run_bump"] is False
    assert decision["skip_bump"] is True
    assert decision["reason"] == "historical_bumper_not_authorized"
    assert decision["runtime_mode"] == sps.RUNTIME_MODE_BUMPER_SUSPENDED
    assert decision["historical_bumper_authorized"] is False


def test_explicit_authorization_required_before_cycle_can_ever_be_returned(isolated_state):
    _mark_super_parrain_write_verified()
    assert sps.decide_super_parrain_action()["action"] != "cycle"

    result = sps.authorize_historical_bumper(
        "operator sign-off after Poulpeo canary review", actor="test-operator"
    )
    assert result["ok"] is True
    assert sps.is_historical_bumper_authorized() is True
    assert sps.super_parrain_runtime_mode() == sps.RUNTIME_MODE_NORMAL_BUMP
    # Stored label refreshed too, not just the live-computed runtime_mode.
    meta = ws.load_write_status()["platforms"]["super-parrain"]
    assert meta["runtime_mode"] == "NORMAL_BUMP"
    assert meta["autonomy"] == "FUSED_UPDATE_BUMP"

    decision = sps.decide_super_parrain_action()
    assert decision["action"] == "cycle"
    assert decision["run_bump"] is True
    assert decision["historical_bumper_authorized"] is True


def test_revoke_returns_bumper_to_fail_closed(isolated_state):
    _mark_super_parrain_write_verified()
    sps.authorize_historical_bumper("test", actor="test-operator")
    assert sps.decide_super_parrain_action()["action"] == "cycle"

    sps.revoke_historical_bumper_authorization(reason="test revoke", actor="test-operator")
    assert sps.is_historical_bumper_authorized() is False
    decision = sps.decide_super_parrain_action()
    assert decision["action"] == "skip"
    assert decision["run_bump"] is False
    meta = ws.load_write_status()["platforms"]["super-parrain"]
    assert meta["runtime_mode"] == "WRITE_VERIFIED_BUMPER_SUSPENDED"
    assert meta["autonomy"] == ws.AUTONOMY_WRITE_VERIFIED_BUMPER_SUSPENDED


def test_missing_phase_flag_defaults_to_not_authorized(isolated_state, tmp_path, monkeypatch):
    """Fail-closed even when the flag has never been written at all."""
    phase_p = tmp_path / "no-phase.json"
    monkeypatch.setattr("lib.phase.PHASE_PATH", phase_p)  # file does not exist
    assert sps.is_historical_bumper_authorized() is False


def test_no_safe_diff_never_downgrades_write_verified_under_new_model(isolated_state):
    _mark_super_parrain_write_verified(program="poulpeo")
    assert ws.get_platform_status("super-parrain") == ws.STATUS_WRITE_VERIFIED

    r2 = ws.mark_sync_verified_no_safe_diff("super-parrain", program="kraken")
    assert r2["ok"] is True
    assert r2["status"] == ws.STATUS_WRITE_VERIFIED
    assert ws.get_platform_status("super-parrain") == ws.STATUS_WRITE_VERIFIED
    assert ws.is_write_verified("super-parrain") is True
    # A NO_SAFE_DIFF record must not touch bumper authorization either way.
    assert sps.is_historical_bumper_authorized() is False
    assert sps.super_parrain_runtime_mode() == sps.RUNTIME_MODE_BUMPER_SUSPENDED


def test_super_parrain_state_paths_are_sandboxed_away_from_repo_data():
    """Meta-regression for the sandboxing itself (tests/conftest.py): every
    path this authorization mechanism touches must resolve outside the real
    repository, with no per-test monkeypatch active -- i.e. under the
    ambient session-wide sandbox alone.
    """
    import lib.phase as phase_mod
    import lib.write_status as ws_mod

    for path in (ws_mod.STATUS_PATH, sps.LAST_SUPER_RUN, sps.PENDING_PATH, phase_mod.PHASE_PATH):
        assert not str(path.resolve()).startswith(str(REPO_ROOT.resolve())), (
            f"{path} resolves inside the real repository -- pytest could write "
            "into production data. tests/conftest.py sandboxing regressed."
        )
