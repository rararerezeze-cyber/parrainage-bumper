"""Politique auth / anti-ban — pure logique."""
from lib.auth_policy import (
    AuthFailureKind,
    classify_auth_failure,
    prefer_official_import,
    session_rules,
    should_retry_login,
    should_stop_platform,
)


def test_session_one_login_per_cycle():
    r = session_rules()
    assert r["logins_per_platform_per_cycle"] == 1
    assert r["login_logout_per_announcement"] is False
    assert r["persist_storage_state_to_repo"] is False
    assert r["max_concurrency"] == 1


def test_captcha_not_same_as_bad_password():
    k = classify_auth_failure("Please complete the CAPTCHA challenge")
    assert k == AuthFailureKind.CAPTCHA_OR_ANTIBOT
    assert should_stop_platform(k) is True
    assert should_retry_login(k) is False


def test_invalid_credentials():
    k = classify_auth_failure("mot de passe incorrect")
    assert k == AuthFailureKind.INVALID_CREDENTIALS
    assert should_retry_login(k) is False


def test_rate_limit():
    k = classify_auth_failure("HTTP 429 too many requests", status_code=429)
    assert k == AuthFailureKind.RATE_LIMIT
    assert should_stop_platform(k) is True


def test_referralcodes_prefers_official_import():
    assert prefer_official_import("referralcodes") is True
    assert prefer_official_import("parrainage-co") is False
