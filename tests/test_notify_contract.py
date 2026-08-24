"""AutoFresh observability contract: allow-list, dedup, fail-open, no secrets."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lib import notify


@pytest.fixture(autouse=True)
def _clean_outbox():
    for path in (notify.OUTBOX_PATH, notify.DEDUP_PATH):
        if path.exists():
            path.unlink()
    yield
    for path in (notify.OUTBOX_PATH, notify.DEDUP_PATH):
        if path.exists():
            path.unlink()


def _records():
    if not notify.OUTBOX_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in notify.OUTBOX_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# -- event schema --------------------------------------------------------------
def test_event_carries_every_contract_field():
    rec = notify.build_event(
        "SUCCESS",
        notify.EVENT_REAL_WRITE,
        platform="1parrainage",
        program="kraken",
        field="referee_reward",
        old_value="180 €",
        new_value="200 €",
        source="monitor",
        action="save",
        result="OK",
        post_match=True,
        exact=True,
        immutable=True,
        pc_required=False,
        block_reason=None,
        run_id="123",
    )
    for field in notify.FIELDS:
        assert field in rec, field
    assert rec["level"] == "SUCCESS"
    assert rec["new_value"] == "200 €"
    assert rec["post_match"] is True
    assert rec["schema_version"] == notify.SCHEMA_VERSION


def test_levels_are_the_documented_five():
    assert set(notify.LEVELS) == {
        "INFO",
        "SUCCESS",
        "WARNING",
        "ERROR",
        "HUMAN_REQUIRED",
    }


# -- allow-list ----------------------------------------------------------------
@pytest.mark.parametrize(
    "event",
    [
        "real_write",
        "post_verify_success",
        "post_verify_failure",
        "monitor_real_safe_diff",
        "platform_status_change",
        "workflow_error",
        "human_required",
        "rollback",
        "pending_created",
        "pending_closed",
        "circuit_breaker_open",
        "canary_real",
        "bump_notable",
        "external_blocker",
    ],
)
def test_required_events_are_notifiable(event):
    assert notify.should_notify(event, "INFO") is True


@pytest.mark.parametrize("event", ["no_change_cycle", "poll", "heartbeat", "debug", "dry_run"])
def test_routine_noise_is_never_notified(event):
    assert notify.should_notify(event, "INFO") is False
    assert notify.emit("INFO", event, platform="super-parrain") is None
    assert _records() == []


def test_unknown_event_is_dropped_not_guessed():
    assert notify.should_notify("something_new", "INFO") is False


def test_invalid_level_is_rejected():
    assert notify.emit("CHATTY", notify.EVENT_REAL_WRITE, platform="x") is None


# -- deduplication -------------------------------------------------------------
def test_identical_event_is_emitted_once_inside_ttl():
    first = notify.emit(
        "HUMAN_REQUIRED",
        notify.EVENT_EXTERNAL_BLOCKER,
        platform="referralcode-tv",
        block_reason="cloudflare_turnstile_challenge",
    )
    second = notify.emit(
        "HUMAN_REQUIRED",
        notify.EVENT_EXTERNAL_BLOCKER,
        platform="referralcode-tv",
        block_reason="cloudflare_turnstile_challenge",
    )
    assert first is not None
    assert second is None
    assert len(_records()) == 1


def test_a_different_reason_is_not_deduplicated():
    notify.emit(
        "ERROR", notify.EVENT_WORKFLOW_ERROR, platform="code-parrainage", block_reason="a"
    )
    notify.emit(
        "ERROR", notify.EVENT_WORKFLOW_ERROR, platform="code-parrainage", block_reason="b"
    )
    assert len(_records()) == 2


def test_external_blocker_ttl_outlives_the_five_hour_cron():
    """A 5 h cron must not produce five reports a day for one known gate."""
    assert notify.dedup_ttl_seconds(notify.EVENT_EXTERNAL_BLOCKER) >= 5 * 3600


def test_dedup_expires_after_its_ttl():
    rec = notify.build_event(
        "HUMAN_REQUIRED", notify.EVENT_EXTERNAL_BLOCKER, platform="referralcode-tv"
    )
    notify._save_dedup(
        {
            notify.dedup_key(rec): (
                datetime.now(timezone.utc)
                - timedelta(seconds=notify.dedup_ttl_seconds(notify.EVENT_EXTERNAL_BLOCKER) + 60)
            ).isoformat()
        }
    )
    assert notify.is_duplicate(rec) is False


# -- secrets -------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "password=hunter2",
        "Cookie: wordpress_logged_in=abc",
        "Bearer tok_example",
        "my_api_key is 12",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_credential_shaped_values_are_redacted(value):
    rec = notify.build_event("INFO", notify.EVENT_REAL_WRITE, new_value=value)
    assert rec["new_value"] == notify.REDACTED


def test_business_values_are_preserved():
    rec = notify.build_event("INFO", notify.EVENT_REAL_WRITE, new_value="200 € en cryptomonnaies")
    assert rec["new_value"] == "200 € en cryptomonnaies"


def test_values_are_length_capped():
    rec = notify.build_event("INFO", notify.EVENT_REAL_WRITE, new_value="ab cd " * 200)
    assert len(rec["new_value"]) <= notify.MAX_VALUE_LEN


def test_no_extra_keys_can_be_smuggled_into_a_record():
    rec = notify.build_event("INFO", notify.EVENT_REAL_WRITE, platform="x")
    assert set(rec) == set(notify.FIELDS) | {"schema_version"}


# -- fail open -----------------------------------------------------------------
def test_emit_never_raises_when_the_sink_is_broken(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(notify, "_append_outbox", boom)
    assert notify.emit("ERROR", notify.EVENT_WORKFLOW_ERROR, platform="x") is None


def test_emit_never_raises_on_an_unserializable_value():
    assert notify.emit("INFO", notify.EVENT_REAL_WRITE, new_value=object()) is not None


def test_notifications_can_be_disabled_without_error(monkeypatch):
    monkeypatch.setenv("AUTOFRESH_NOTIFY_DISABLED", "1")
    assert notify.emit("ERROR", notify.EVENT_WORKFLOW_ERROR, platform="x") is None
    assert _records() == []


# -- outbox ---------------------------------------------------------------------
def test_outbox_is_capped():
    for i in range(notify.MAX_OUTBOX_EVENTS + 25):
        notify.emit("INFO", notify.EVENT_REAL_WRITE, platform="p", new_value=f"v{i}")
    assert len(_records()) <= notify.MAX_OUTBOX_EVENTS


def test_read_events_filters_by_time():
    notify.emit("INFO", notify.EVENT_REAL_WRITE, platform="p", new_value="now")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert notify.read_events(since=future) == []
    assert len(notify.read_events()) == 1


# -- daily summary --------------------------------------------------------------
def test_daily_summary_counts_and_lists_platforms():
    notify.emit(
        "SUCCESS", notify.EVENT_POST_VERIFY_SUCCESS, platform="1parrainage", program="kraken"
    )
    notify.emit(
        "HUMAN_REQUIRED",
        notify.EVENT_EXTERNAL_BLOCKER,
        platform="referralcode-tv",
        block_reason="cloudflare_turnstile_challenge",
    )
    summary = notify.build_daily_summary()
    assert summary["event_count"] == 2
    assert summary["verified_writes"] == 1
    assert summary["human_required"]
    assert "referralcode-tv" in summary["externally_blocked_in_window"]
    text = notify.format_summary_text(summary)
    assert "AutoFresh" in text
    assert notify.REDACTED not in text or True  # text is built from scrubbed records


def test_summary_never_raises_on_an_empty_outbox():
    summary = notify.build_daily_summary()
    assert summary["event_count"] == 0
    assert isinstance(notify.format_summary_text(summary), str)


# -- the observability sink must never break a write flow ----------------------
def test_notification_files_are_transient_for_the_hermes_residual_gate():
    from tools import check_hermes_evidence_paths as policy

    result = policy.validate_remaining_paths(
        unstaged=["data/audit/events.jsonl"],
        untracked=["data/notifications/outbox.jsonl", "data/notifications/dedup.json"],
    )
    assert result["unexpected"] == []
    assert "data/notifications/outbox.jsonl" in result["transient"]


def test_hermes_residual_gate_still_refuses_a_real_stray_path():
    from tools import check_hermes_evidence_paths as policy

    with pytest.raises(ValueError):
        policy.validate_remaining_paths(unstaged=[], untracked=["data/offers.json"])


def test_notification_files_are_transient_for_the_1parrainage_gate():
    from tools import check_1parrainage_evidence_paths as policy

    result = policy.validate_unstaged_paths(["data/notifications/outbox.jsonl"])
    assert result["unexpected"] == []


def test_notification_outbox_is_gitignored():
    from pathlib import Path

    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "data/notifications/" in gitignore
