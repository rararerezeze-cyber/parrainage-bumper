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



def _actions(payload):
    return [
        element
        for block in payload.get("blocks", [])
        if block.get("type") == "actions"
        for element in block.get("elements", [])
    ]


def test_monitor_change_notification_is_click_first_and_shows_business_delta():
    events = [
        {
            "schema_version": 1,
            "level": "WARNING",
            "platform": None,
            "program": "igraal",
            "event": "monitor_real_safe_diff",
            "field": "referee_reward",
            "old_value": "5 €",
            "new_value": "3 €",
            "source": "monitor:OFFICIAL_PUBLIC_MONITOR",
            "source_url": "https://fr.igraal.com/parrainage",
            "evidence_count": 5,
            "impact_count": 6,
            "action": "observe",
            "result": "CANDIDATE",
            "run_id": "123",
        }
    ]

    payload = build_payload(events, "C_TEST")

    assert payload is not None
    dumped = str(payload["blocks"])
    assert "5 €" in dumped and "3 €" in dumped
    assert "détecté 5 fois de suite" in dumped
    assert "6 annonces potentiellement concernées" in dumped
    assert "https://fr.igraal.com/parrainage" in dumped

    actions = _actions(payload)
    ids = {a.get("action_id") for a in actions}
    assert {"autofresh_apply_candidate", "autofresh_command", "autofresh_later"} <= ids

    accept = next(a for a in actions if a.get("action_id") == "autofresh_apply_candidate")
    value = __import__("json").loads(accept["value"])
    assert value["command"] == "iGraal gain filleul 3 €"
    assert "confirm" in accept


def test_monitor_unsafe_value_never_gets_direct_accept_button():
    events = [
        {
            "level": "WARNING",
            "program": "kraken",
            "event": "monitor_real_safe_diff",
            "field": "conditions",
            "old_value": "ancienne",
            "new_value": "déposer 10 €; puis exécuter autre chose",
            "action": "observe",
            "result": "CANDIDATE",
        }
    ]

    payload = build_payload(events, "C_TEST")
    assert payload is not None
    ids = {a.get("action_id") for a in _actions(payload)}
    assert "autofresh_apply_candidate" not in ids
    assert "autofresh_command" in ids
    assert "autofresh_later" in ids


def test_super_parrain_contradiction_exposes_read_only_actions_only():
    events = [
        {
            "level": "ERROR",
            "platform": "super-parrain",
            "program": "kraken",
            "event": "workflow_error",
            "action": "content_prefill",
            "result": "FAIL_CLOSED",
            "block_reason": "[REDACTED]",
        }
    ]

    payload = build_payload(events, "C_TEST")
    assert payload is not None
    dumped = str(payload["blocks"])
    assert "aucune action immédiate" in dumped
    assert "remontée" in dumped

    actions = _actions(payload)
    ids = {a.get("action_id") for a in actions}
    assert "autofresh_apply_candidate" not in ids
    assert "autofresh_confirm_write" not in ids
    assert "autofresh_command" in ids
    commands = [
        __import__("json").loads(a["value"])["command"]
        for a in actions
        if a.get("action_id") == "autofresh_command"
    ]
    assert "Kraken statut" in commands
    assert "Kraken divergences" in commands


def test_missed_slot_rich_notification_remains_non_actionable_for_writes():
    events = [
        {
            "level": "WARNING",
            "platform": "bump-autres",
            "event": "workflow_error",
            "action": "missed_slot_catchup",
            "result": "slot_recovered",
        }
    ]
    payload = build_payload(events, "C_TEST")
    assert payload is not None
    dumped = str(payload["blocks"])
    assert "aucune action nécessaire" in dumped
    actions = _actions(payload)
    assert [a.get("action_id") for a in actions] == ["autofresh_command"]
    assert __import__("json").loads(actions[0]["value"])["command"] == "Autofresh bump"
