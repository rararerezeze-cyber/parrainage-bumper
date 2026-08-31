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
