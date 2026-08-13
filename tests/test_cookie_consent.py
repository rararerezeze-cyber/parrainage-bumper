"""Cookie-consent label classification — no network."""
from lib.cookie_consent import classify_consent_label, pick_accept_button


def test_accept_labels():
    assert classify_consent_label("Tout accepter") == "ACCEPT"
    assert classify_consent_label("Accepter") == "ACCEPT"
    assert classify_consent_label("J'accepte") == "ACCEPT"
    assert classify_consent_label("Autoriser") == "ACCEPT"
    assert classify_consent_label("Accept all") == "ACCEPT"


def test_reject_and_settings_not_accept():
    assert classify_consent_label("Tout refuser") == "REJECT_OR_SETTINGS"
    assert classify_consent_label("Personnaliser") == "REJECT_OR_SETTINGS"
    assert classify_consent_label("Accepter uniquement les nécessaires") == "REJECT_OR_SETTINGS"


def test_pick_prefers_accept_all():
    picked = pick_accept_button(
        [{"text": "Accepter"}, {"text": "Tout accepter"}, {"text": "Personnaliser"}]
    )
    assert picked is not None
    assert picked["text"] == "Tout accepter"
