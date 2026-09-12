from tools.notify_slack import build_payload


def test_build_payload_ignores_outbox_metadata_fields():
    events = [
        {
            "schema_version": 1,
            "level": "HUMAN_REQUIRED",
            "platform": "referralcode-tv",
            "program": None,
            "event": "external_blocker",
            "action": "scheduled_bump",
            "result": "EXPECTED_EXTERNAL_BLOCKER",
            "block_reason": "cloudflare_turnstile_challenge",
            "pc_required": True,
            "source": "bumper.main",
            "timestamp": "2026-09-11T09:52:46+00:00",
            "run_id": "34586371861",
        }
    ]

    payload = build_payload(events, "C_TEST")

    assert payload is not None
    assert payload["channel"] == "C_TEST"
    assert "🖐️" in payload["text"]
    assert "ReferralCode.tv" in payload["text"]
    assert "blocage externe" in payload["text"]
    assert "challenge Cloudflare Turnstile" in payload["text"]
    assert "schema_version" not in payload["text"]



def test_content_prefill_fail_closed_is_explained_in_plain_french():
    events = [
        {
            "schema_version": 1,
            "level": "ERROR",
            "platform": "super-parrain",
            "program": "kraken",
            "event": "workflow_error",
            "action": "content_prefill",
            "result": "FAIL_CLOSED",
            "block_reason": "[REDACTED]",
            "source": "platforms.super_parrain.prefill",
        }
    ]

    payload = build_payload(events, "C_TEST")

    assert payload is not None
    text = payload["text"]
    assert "Super-Parrain / Kraken" in text
    assert "mise à jour du contenu a été annulée par sécurité" in text
    assert "bumper continue" in text
    assert "[REDACTED]" not in text
    assert "FAIL_CLOSED" not in text


def test_missed_slot_notification_says_it_was_recovered_and_needs_no_action():
    events = [
        {
            "schema_version": 1,
            "level": "WARNING",
            "platform": "bump-autres",
            "event": "workflow_error",
            "action": "missed_slot_catchup",
            "result": "slot_1_dispatched_over_20min_late",
            "block_reason": "bump_autres_missed_slot",
        }
    ]

    payload = build_payload(events, "C_TEST")

    assert payload is not None
    text = payload["text"]
    assert "rattrapé automatiquement" in text
    assert "Aucune action nécessaire" in text
    assert "missed_slot" not in text


def test_monitor_change_notification_explains_no_write_and_gives_command():
    events = [
        {
            "schema_version": 1,
            "level": "WARNING",
            "platform": None,
            "program": "kraken",
            "event": "monitor_real_safe_diff",
            "action": "candidate",
            "result": "REAL_SAFE_DIFF",
        }
    ]

    payload = build_payload(events, "C_TEST")

    assert payload is not None
    text = payload["text"]
    assert "Kraken" in text
    assert "Aucune modification n'a été faite" in text
    assert "/autofresh Kraken divergences" in text
    assert "candidate" not in text
