"""Tests for lib.slack_format — Slack Block Kit rendering of Autofresh
operator results. Pure functions, no network, no Slack client.
"""
from __future__ import annotations

import json

from lib.slack_format import notification_text, render_result


def _base_result(**overrides):
    result = {
        "ok": True,
        "action": "autofresh",
        "command": "Kraken statut",
        "parsed": {"action": "status", "program": "kraken", "field": None, "platform": None},
        "correlation_id": "corr-1",
        "auth": {"identity": "slack:U123"},
        "human_summary": "Kraken — 6 plateformes mappées, 6 en attente, 0 synchronisée.",
        "platforms": [],
        "errors": [],
    }
    result.update(overrides)
    return result


def test_notification_text_is_short_and_single_line():
    text = notification_text(_base_result())
    assert len(text) <= 160
    assert "\n" not in text


def test_notification_text_uses_first_error_on_failure():
    result = _base_result(ok=False, errors=[{"code": "parse_error", "detail": "commande inconnue"}])
    text = notification_text(result)
    assert "commande inconnue" in text
    assert "échec" in text.lower()


def test_notification_text_never_exceeds_max_even_for_huge_summary():
    huge = "Voici un long récapitulatif. " * 40
    result = _base_result(human_summary=huge)
    text = notification_text(result)
    assert len(text) <= 160


def test_render_result_returns_text_and_blocks():
    payload = render_result(_base_result())
    assert "text" in payload and "blocks" in payload
    assert isinstance(payload["blocks"], list)
    assert payload["blocks"][0]["type"] == "header"


def test_render_result_header_reflects_failure():
    payload = render_result(_base_result(ok=False, errors=[{"code": "x", "detail": "y"}]))
    header_text = payload["blocks"][0]["text"]["text"]
    assert header_text.startswith("❌")


def test_render_result_header_reflects_success():
    payload = render_result(_base_result())
    header_text = payload["blocks"][0]["text"]["text"]
    assert header_text.startswith("✅")


def test_render_result_includes_full_human_summary_in_blocks_even_if_long():
    long_summary = "x" * 5000
    payload = render_result(_base_result(human_summary=long_summary))
    joined = "".join(
        b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section" and "text" in b
    )
    assert len(joined) >= 5000  # full content preserved across chunked blocks (unlike `text`)


def test_render_result_includes_errors_block_when_present():
    payload = render_result(_base_result(ok=False, errors=[{"code": "unauthorized", "detail": "no token"}]))
    texts = [b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section"]
    assert any("unauthorized" in t for t in texts)


def test_render_result_includes_platform_rows():
    platforms = [
        {"platform": "kraken-super-parrain", "status": "pending_update", "can_auto_write": False, "route": "HUMAN_SAVE_REQUIRED", "changed_fields": {"referee_reward": {}}},
        {"platform": "1parrainage", "status": "pending_update", "can_auto_write": True, "route": "AUTO_ON_SAFE_DIFF", "changed_fields": {"referee_reward": {}}},
    ]
    payload = render_result(_base_result(platforms=platforms))
    section_texts = "\n".join(
        b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section" and "text" in b
    )
    assert "1parrainage" in section_texts
    assert "kraken-super-parrain" in section_texts


def test_render_result_adds_confirm_button_when_writer_eligible_platform_pending():
    platforms = [
        {"platform": "1parrainage", "status": "pending_update", "can_auto_write": True, "route": "AUTO_ON_SAFE_DIFF", "changed_fields": {"referee_reward": {}}},
    ]
    payload = render_result(_base_result(platforms=platforms), run_writers_requested=False)
    action_blocks = [b for b in payload["blocks"] if b.get("type") == "actions"]
    assert len(action_blocks) == 1
    button = action_blocks[0]["elements"][0]
    assert button["action_id"] == "autofresh_confirm_write"
    value = json.loads(button["value"])
    assert value["command"] == "Kraken statut"
    assert value["correlation_id"] == "corr-1"


def test_render_result_no_confirm_button_when_run_writers_already_requested():
    platforms = [
        {"platform": "1parrainage", "status": "pending_update", "can_auto_write": True, "route": "AUTO_ON_SAFE_DIFF", "changed_fields": {}},
    ]
    payload = render_result(_base_result(platforms=platforms), run_writers_requested=True)
    assert not [b for b in payload["blocks"] if b.get("type") == "actions"]


def test_render_result_no_confirm_button_when_no_platform_eligible():
    platforms = [
        {"platform": "referraldrop", "status": "blocked", "can_auto_write": False, "route": "AUTH_BLOCKED_MANUAL", "changed_fields": {}},
    ]
    payload = render_result(_base_result(platforms=platforms))
    assert not [b for b in payload["blocks"] if b.get("type") == "actions"]


def test_render_result_no_confirm_button_when_result_not_ok():
    platforms = [
        {"platform": "1parrainage", "status": "pending_update", "can_auto_write": True, "route": "AUTO_ON_SAFE_DIFF", "changed_fields": {}},
    ]
    payload = render_result(_base_result(ok=False, errors=[{"code": "x", "detail": "y"}], platforms=platforms))
    assert not [b for b in payload["blocks"] if b.get("type") == "actions"]


def test_render_result_includes_correlation_id_and_requester_context():
    payload = render_result(_base_result())
    ctx = [b for b in payload["blocks"] if b.get("type") == "context"]
    assert ctx
    text = ctx[0]["elements"][0]["text"]
    assert "corr-1" in text
    assert "slack:U123" in text


def test_render_result_never_leaks_secret_shaped_tokens():
    payload = render_result(_base_result(human_summary="run_writers=false. pending_update."))
    dumped = json.dumps(payload)
    assert "xoxb-" not in dumped
    assert "xoxa-" not in dumped


# --- Real-shape regression tests (2026-08-31 live E2E finding): a "status"
# result's raw human_summary literally embeds a JSON status dump (see
# lib.hermes_interface._run_autofresh_command_locked) and a "set"/"remove"
# result's embeds full cross-platform prose + raw local shell commands
# (e.g. "python -u tools/local_headed_rctv_canary.py") + legacy "Telegram
# live-capable" wording -- none of that belongs in Slack's primary view.
# help/divergences/plateformes are untouched: AGENTS.md requires their
# human_summary be relayed verbatim.

def _status_result(**overrides):
    result = _base_result(
        parsed={"action": "status", "program": "kraken", "field": None, "platform": None},
        human_summary=(
            "kraken status\n\n"
            '{\n  "WRITE_VERIFIED": "5/7",\n  "telegram_live_capable": ["parrainage-co"]\n}'
        ),
        plan={"summary": {"platforms_mapped": 6, "pending_update": 6, "in_sync": 0}},
        routing={
            "automatic_safe_diff_targets": ["1parrainage", "code-parrainage", "parrainage-co"],
            "human_routed_targets": [
                {"platform": "referralcode-tv", "route": "HUMAN_SAVE_REQUIRED", "command": "python -u tools/local_headed_rctv_canary.py"}
            ],
            "blocked_targets": ["referralcodes", "super-parrain", "referraldrop"],
        },
    )
    result.update(overrides)
    return result


def test_status_result_gets_a_concise_french_summary_not_a_raw_json_dump():
    payload = render_result(_status_result())
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "WRITE_VERIFIED" not in sections
    assert '"telegram_live_capable"' not in sections
    assert "Kraken" in sections
    assert "6 plateforme(s) à mettre à jour sur 6" in sections


def test_status_result_summary_never_leaks_raw_local_shell_command():
    payload = render_result(_status_result())
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "local_headed_rctv_canary" not in sections
    assert "referralcode-tv" in sections  # platform named, just not the raw command


def test_status_result_summary_names_auto_and_human_targets_in_french():
    payload = render_result(_status_result())
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "Écriture automatique possible" in sections
    assert "1parrainage" in sections
    assert "Action manuelle requise" in sections


def _set_result(**overrides):
    result = _base_result(
        parsed={"action": "set", "program": "kraken", "field": "personal_code", "platform": None},
        human_summary="kraken personal_code → 'TESTE2E999'  [GLOBAL]\nsource effective: GLOBAL_OPERATOR_OVERRIDE\n\nCross-platform impact:\n  1parrainage ...",
        result={"action": "set", "old_effective": "cpbrgddy", "new_effective": "TESTE2E999", "new_source": "GLOBAL_OPERATOR_OVERRIDE"},
    )
    result.update(overrides)
    return result


def test_set_result_gets_a_concise_old_to_new_line_not_full_prose():
    payload = render_result(_set_result())
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "cpbrgddy" in sections and "TESTE2E999" in sections
    assert "Cross-platform impact" not in sections
    assert "GLOBAL" not in sections or "globale" in sections  # curated wording, not the raw bracketed tag


def test_set_result_shows_platform_scope_when_platform_specific():
    payload = render_result(_set_result(parsed={"action": "set", "program": "kraken", "field": "personal_code", "platform": "super-parrain"}))
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "plateforme super-parrain" in sections


def test_remove_result_gets_a_concise_line():
    payload = render_result(_base_result(
        parsed={"action": "remove", "program": "kraken", "field": "personal_code", "platform": None},
        human_summary="removed override kraken personal_code ...",
    ))
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "override supprimé" in sections
    assert "personal_code" in sections


def test_meta_read_actions_still_relay_human_summary_verbatim():
    """help/divergences/plateformes: AGENTS.md requires the exact text,
    unmodified -- must NOT be replaced by a synthesized concise summary."""
    curated_text = "Autofresh — commandes disponibles :\n- Kraken statut\n- Kraken overrides"
    payload = render_result(_base_result(
        parsed={"action": "help", "program": None, "field": None, "platform": None},
        human_summary=curated_text,
    ))
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert curated_text in sections


def test_failed_result_still_shows_human_summary_or_errors_not_blank():
    payload = render_result(_status_result(ok=False, errors=[{"code": "unauthorized", "detail": "no token"}]))
    sections = "\n".join(b["text"]["text"] for b in payload["blocks"] if b.get("type") == "section")
    assert "unauthorized" in sections


def test_notification_text_for_set_result_is_clean_no_stray_markdown_or_duplication():
    """Regression: an earlier version produced 'kraken set — Kraken* — ...'
    (leftover asterisk + duplicated program label) once the concise
    summary already embeds the program name."""
    text = notification_text(_set_result())
    assert text.count("Kraken") == 1
    assert "*" not in text
    assert not text.lower().startswith("kraken set —")
