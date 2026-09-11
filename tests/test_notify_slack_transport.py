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
