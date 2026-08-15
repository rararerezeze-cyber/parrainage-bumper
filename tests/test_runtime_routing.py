"""Monitor → Hermes → writer routing. No live write."""
from __future__ import annotations

from lib.write_status import (
    ROUTE_AUTO_ON_SAFE_DIFF,
    ROUTE_AUTH_BLOCKED_MANUAL,
    ROUTE_BUMPER_NOT_AUTHORIZED,
    ROUTE_CANARY_PENDING_SKIP,
    ROUTE_COOKIE_SESSION_NOT_PC_OFF,
    ROUTE_HUMAN_SAVE_REQUIRED,
    ROUTE_NEVER_AUTO_COMMIT,
    human_local_command,
    may_auto_execute_on_safe_diff,
    runtime_route,
)
from platforms.referralcode_tv.writer import execute_write as rctv_execute
from platforms.referralcodes.writer import execute_write as rcodes_execute
from tools.run_verified_writers import AUTO_SAFE_DIFF_PLATFORMS, NEVER_AUTO_DISPATCH, _skip_route


def test_pc_off_ready_auto_only_on_safe_diff(status_path=None):
    assert runtime_route("1parrainage") == ROUTE_AUTO_ON_SAFE_DIFF
    assert runtime_route("code-parrainage") == ROUTE_AUTO_ON_SAFE_DIFF
    assert runtime_route("parrainage-co") == ROUTE_AUTO_ON_SAFE_DIFF
    assert may_auto_execute_on_safe_diff("1parrainage") is True
    assert may_auto_execute_on_safe_diff("code-parrainage") is True
    assert may_auto_execute_on_safe_diff("parrainage-co") is True


def test_human_and_blocked_never_auto():
    assert runtime_route("referralcode-tv") == ROUTE_HUMAN_SAVE_REQUIRED
    assert may_auto_execute_on_safe_diff("referralcode-tv") is False
    assert human_local_command("referralcode-tv") == "python -u tools/local_headed_rctv_canary.py"
    assert runtime_route("referralcodes") == ROUTE_NEVER_AUTO_COMMIT
    assert may_auto_execute_on_safe_diff("referralcodes") is False
    assert runtime_route("referraldrop") == ROUTE_AUTH_BLOCKED_MANUAL
    assert may_auto_execute_on_safe_diff("referraldrop") is False
    # super-parrain's exact route depends on two independent, legitimately
    # time-varying gates: whether the content-writer is WRITE_VERIFIED
    # (ROUTE_CANARY_PENDING_SKIP if not) and, once verified, whether the
    # separate global historical bumper has been explicitly authorized
    # (ROUTE_BUMPER_NOT_AUTHORIZED if not — fail-closed default). The
    # invariant that must always hold regardless of that real, evolving
    # state is that it is never auto-dispatched.
    assert runtime_route("super-parrain") in (
        ROUTE_CANARY_PENDING_SKIP,
        ROUTE_BUMPER_NOT_AUTHORIZED,
    )
    assert runtime_route("super-parrain") != ROUTE_AUTO_ON_SAFE_DIFF
    assert may_auto_execute_on_safe_diff("super-parrain") is False
    assert runtime_route("parrainage-co") == ROUTE_AUTO_ON_SAFE_DIFF
    assert may_auto_execute_on_safe_diff("parrainage-co") is True


def test_writers_refuse_human_and_commit():
    r = rctv_execute()
    assert r["ok"] is False
    assert r["write_mode"] == "HUMAN_SAVE_REQUIRED"
    c = rcodes_execute("kraken", dry_run=False)
    assert c["ok"] is False
    assert "NEVER_AUTO_COMMIT" in c["error"]


def test_dispatch_lists_do_not_include_humans():
    assert "referralcode-tv" not in AUTO_SAFE_DIFF_PLATFORMS
    assert "referralcodes" not in AUTO_SAFE_DIFF_PLATFORMS
    assert "referraldrop" not in AUTO_SAFE_DIFF_PLATFORMS
    assert "super-parrain" not in AUTO_SAFE_DIFF_PLATFORMS
    assert "parrainage-co" in AUTO_SAFE_DIFF_PLATFORMS
    assert may_auto_execute_on_safe_diff("parrainage-co") is True
    for plat in NEVER_AUTO_DISPATCH:
        skip = _skip_route(plat)
        assert skip["skipped"] is True
        assert skip["reason"] != ROUTE_AUTO_ON_SAFE_DIFF
