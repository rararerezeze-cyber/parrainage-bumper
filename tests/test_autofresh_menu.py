"""French Telegram UX layer: Autofresh menu/aide/exemples/plateformes,
French verb/field variants, ambiguous-field clarification, and the
never-a-writer invariant for every read-only meta-command.
"""
from __future__ import annotations

import pytest

from lib.autofresh_help import (
    TOPIC_EXEMPLES,
    TOPIC_MENU,
    TOPIC_PLATEFORMES,
    ambiguous_field_reply,
    detect_meta_command,
)
from lib.hermes_interface import run_autofresh_command
from lib.offers import OffersRepository
from tools.telegram_update import apply_operator_command, parse_message

FIXTURE_OFFERS = None  # resolved via default OffersRepository (real offers.json has kraken)


@pytest.fixture(autouse=True)
def _local_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOFRESH_ALLOW_LOCAL_OPERATOR", "1")
    monkeypatch.delenv("AUTOFRESH_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_SHARED_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    ov = tmp_path / "operator-overrides.json"
    ov.write_text('{"version":1,"overrides":[]}\n', encoding="utf-8")
    monkeypatch.setattr("lib.operator_overrides.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr("lib.hermes_interface.OPERATOR_OVERRIDES_PATH", ov)
    monkeypatch.setattr(
        "lib.hermes_interface.RESULT_PATH", tmp_path / "hermes-last-result.json"
    )
    monkeypatch.setattr("lib.safety.snapshot_state", lambda *_a, **_k: {"id": "test"})


# --- detect_meta_command -----------------------------------------------

@pytest.mark.parametrize(
    "raw,expected_topic",
    [
        ("Autofresh", TOPIC_MENU),
        ("autofresh", TOPIC_MENU),
        ("  Autofresh  ", TOPIC_MENU),
        ("Autofresh aide", TOPIC_MENU),
        ("Aide Autofresh", TOPIC_MENU),
        ("Autofresh commandes", TOPIC_MENU),
        ("AUTOFRESH COMMANDES", TOPIC_MENU),
        ("Autofresh exemples", TOPIC_EXEMPLES),
        ("Autofresh plateformes", TOPIC_PLATEFORMES),
    ],
)
def test_detect_meta_command_matches(raw, expected_topic):
    assert detect_meta_command(raw) == expected_topic


@pytest.mark.parametrize(
    "raw",
    [
        "Kraken status",
        "Kraken gain filleul 20 €",
        "quelque chose d'autre",
        "",
    ],
)
def test_detect_meta_command_does_not_shadow_real_commands(raw):
    assert detect_meta_command(raw) is None


# --- ambiguous field clarify ---------------------------------------------

def test_ambiguous_field_reply_for_bare_recompense():
    hint = ambiguous_field_reply("récompense")
    assert hint is not None
    assert "gain filleul" in hint.lower()
    assert "gain parrain" in hint.lower()


def test_ambiguous_field_reply_none_for_known_alias():
    # "reward" is an existing resolved alias (referee_reward) -- must not
    # be reclassified as ambiguous, that would be a behavior change.
    assert ambiguous_field_reply("reward") is None


def test_kraken_recompense_gives_friendly_clarify_not_raw_error():
    offers = OffersRepository()
    with pytest.raises(ValueError) as excinfo:
        parse_message("Kraken récompense", offers)
    msg = str(excinfo.value)
    assert "gain filleul" in msg.lower()
    assert "unknown_field" not in msg


# --- parse_message: meta-commands + French verbs --------------------------

def test_parse_message_autofresh_returns_help_action():
    offers = OffersRepository()
    parsed = parse_message("Autofresh", offers)
    assert parsed["action"] == "help"
    assert parsed["help_topic"] == TOPIC_MENU
    assert parsed["program"] is None


def test_parse_message_kraken_statut_same_as_status():
    offers = OffersRepository()
    p_en = parse_message("Kraken status", offers)
    p_fr = parse_message("Kraken statut", offers)
    assert p_en["action"] == p_fr["action"] == "status"
    assert p_en["program"] == p_fr["program"] == "kraken"


def test_parse_message_kraken_divergences():
    offers = OffersRepository()
    parsed = parse_message("Kraken divergences", offers)
    assert parsed["action"] == "divergences"
    assert parsed["program"] == "kraken"


def test_parse_message_kraken_plateformes():
    offers = OffersRepository()
    parsed = parse_message("Kraken plateformes", offers)
    assert parsed["action"] == "plateformes_program"
    assert parsed["program"] == "kraken"


def test_unknown_command_raises_cleanly_not_a_crash():
    offers = OffersRepository()
    with pytest.raises(ValueError):
        parse_message("ceci ne veut rien dire du tout", offers)


# --- apply_operator_command: help/divergences/plateformes never mutate ----

def test_apply_help_returns_menu_text_and_never_touches_store():
    parsed = {"action": "help", "help_topic": TOPIC_MENU, "program": None}
    result = apply_operator_command(parsed, message="Autofresh")
    assert result["action"] == "help"
    assert "AUTOFRESH" in result["text"]


def test_apply_divergences_for_program_with_no_pending_candidates():
    offers = OffersRepository()
    parsed = parse_message("Kraken divergences", offers)
    result = apply_operator_command(parsed, message="Kraken divergences")
    assert result["action"] == "divergences"
    assert result["divergences"] == []


# --- end-to-end via run_autofresh_command: always read-only ---------------

@pytest.mark.parametrize(
    "command",
    [
        "Autofresh",
        "Autofresh aide",
        "Autofresh commandes",
        "Aide Autofresh",
        "Autofresh exemples",
        "Autofresh plateformes",
        "Kraken statut",
        "Kraken status",
        "Kraken divergences",
        "Kraken plateformes",
    ],
)
def test_read_only_commands_never_persist_or_write(monkeypatch, command):
    """Even with run_writers=True and persist=True, none of these may ever
    touch the override store or spawn the verified-writers subprocess."""
    writer_calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: writer_calls.append((a, k)) or pytest.fail("writer subprocess invoked"),
    )
    result = run_autofresh_command(
        command,
        requester={"source": "test"},
        persist=True,
        plan=True,
        run_writers=True,
    )
    assert result["ok"] is True
    assert result["human_summary"]
    assert writer_calls == []
    # No override mutation: precedence-chain read stays CANONICAL/absent.
    from lib.operator_overrides import OperatorOverrideStore

    assert OperatorOverrideStore().load() == []


def test_status_command_result_is_read_only_shape():
    result = run_autofresh_command(
        "Kraken status",
        requester={"source": "test"},
        persist=True,
        plan=True,
        run_writers=True,
    )
    assert result["ok"] is True
    assert result["parsed"]["action"] == "status"
    # status never produces a writers report
    assert result.get("writers") is None


def test_menu_mentions_french_variants_help_text():
    result = run_autofresh_command(
        "Autofresh",
        requester={"source": "test"},
        persist=True,
        plan=True,
        run_writers=False,
    )
    text = result["human_summary"]
    for needle in ("statut", "gain filleul", "gain parrain", "supprimer"):
        assert needle in text.lower()


def test_plateformes_topic_lists_all_seven_platforms():
    from lib.autofresh_help import build_platforms_status
    from lib.write_status import ALL_PLATFORMS, format_telegram_platform_lines  # noqa: F401

    text = build_platforms_status()
    assert text.count("•") == len(ALL_PLATFORMS)
