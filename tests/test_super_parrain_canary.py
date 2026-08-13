"""Politique canary Super-Parrain — tests pure logique (pas de navigateur)."""
from lib.super_parrain_policy import (
    autofresh_enabled,
    parse_canary_programs,
    policy_snapshot,
    should_prefill_content,
)
from lib.super_parrain_post_verify import check_fields_in_text, fields_match_ok, verify_public_program


def test_default_canary_is_kraken_only():
    env = {"AUTOFRESH_SUPER": "1"}  # no MODE → canary default
    allowed = parse_canary_programs(env)
    assert allowed == frozenset({"kraken"})
    ok, reason = should_prefill_content("kraken", env)
    assert ok and reason == "canary"
    ok2, reason2 = should_prefill_content("coinbase", env)
    assert not ok2 and reason2 == "bump_only_not_canary"
    ok3, reason3 = should_prefill_content("revolut", env)
    assert not ok3


def test_explicit_canary_list():
    env = {"AUTOFRESH_MODE": "canary", "AUTOFRESH_CANARY_PROGRAMS": "kraken,coinbase"}
    assert parse_canary_programs(env) == frozenset({"kraken", "coinbase"})
    assert should_prefill_content("coinbase", env)[0]
    assert not should_prefill_content("binance", env)[0]


def test_full_rollout_modes():
    for env in (
        {"AUTOFRESH_MODE": "full"},
        {"AUTOFRESH_MODE": "canary", "AUTOFRESH_CANARY_PROGRAMS": "*"},
        {"AUTOFRESH_MODE": "all"},
    ):
        assert parse_canary_programs(env) is None
        assert should_prefill_content("any-program", env)[0]


def test_autofresh_off():
    env = {"AUTOFRESH_SUPER": "0"}
    assert not autofresh_enabled(env)
    assert not should_prefill_content("kraken", env)[0]


def test_stop_flag_blocks_prefill():
    env = {
        "AUTOFRESH_SUPER": "1",
        "AUTOFRESH_MODE": "canary",
        "AUTOFRESH_CANARY_PROGRAMS": "kraken",
        "AUTOFRESH_STOP": "1",
    }
    ok, reason = should_prefill_content("kraken", env)
    assert not ok
    assert reason == "autofresh_stopped_after_canary_fail"


def test_policy_snapshot():
    snap = policy_snapshot({"AUTOFRESH_SUPER": "1", "AUTOFRESH_MODE": "canary"})
    assert snap["canary_programs"] == ["kraken"]
    assert snap["autofresh_enabled"] is True


def test_field_checks_detect_code_update():
    published = "Code parrain : 4hpz4gdy\nLien : https://proinvite.kraken.com/9f1e/lqbuov8u"
    checks = check_fields_in_text(
        published,
        {
            "personal_code": "4hpz4gdy",
            "personal_link": "https://proinvite.kraken.com/9f1e/lqbuov8u",
        },
        {
            "personal_code": "cpbrgddy",
            "personal_link": "https://invite.kraken.com/JDNW/s5qudqe4",
        },
    )
    assert fields_match_ok(checks)
    assert checks["personal_code"]["present"] is True
    assert checks["personal_code"].get("old_still_present") is False


def test_field_checks_fail_when_old_code_remains():
    published = "Code parrain : cpbrgddy"
    checks = check_fields_in_text(
        published,
        {"personal_code": "4hpz4gdy"},
        {"personal_code": "cpbrgddy"},
    )
    assert not fields_match_ok(checks)


def test_verify_public_with_override_text():
    # No network: inject published text matching desired Kraken code from offers
    from lib.super_parrain_content import get_desired_content

    d = get_desired_content("kraken")
    assert d.code
    # Build a synthetic public page body with desired values
    pub = f"Code : {d.code}\nLien : {d.link}\nBonus : {d.reward}\ndiscord.gg/dDEMb6jEbn"
    result = verify_public_program(
        "kraken",
        fetch=False,
        published_override=pub,
        filled_fields=["code", "link"],
    )
    assert result.post_match is True
    assert result.ok is True
