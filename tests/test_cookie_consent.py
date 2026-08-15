"""Cookie-consent label classification — no network."""
import asyncio

from lib import cookie_consent as cc
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


class _FakePage:
    frames: list = []


def test_handle_cookie_consent_polls_for_late_rendering_button(monkeypatch):
    """Regression: keep polling while a banner is visible but no accept
    button has rendered yet, instead of giving up after one fixed-delay
    check gated on the login field's own visibility.

    Evidence: 1parrainage GH Actions DOM census (data/captures/
    1parrainage-edit-map.json, 2026-08-15) showed input#_username
    visible=1 from the very first census -- alongside a Sourcepoint
    consent banner (sd-cmp-* nodes) whose Accept button had not rendered
    yet within the single original fixed-delay scan, raising
    CONSENT_BLOCKED even though the field being "visible" said nothing
    about whether the CMP widget itself was ready.
    """
    calls = {"scan": 0}
    state = {"clicked": False}
    clicked: dict = {}

    async def fake_username_visible(page):
        return True  # always true on this site, consent-widget state or not

    async def fake_consent_ui_visible(page):
        return not state["clicked"]

    async def fake_visible_buttons(owner):
        calls["scan"] += 1
        if calls["scan"] < 3:
            return []  # not rendered yet
        return [{"text": "Tout accepter", "id": "", "visible": True}]

    async def fake_click_known_accept(page):
        return None  # no known CMP selector id matched

    async def fake_click_in_owner(owner, button):
        clicked["button"] = button
        state["clicked"] = True
        return True

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_consent_ui_visible", fake_consent_ui_visible)
    monkeypatch.setattr(cc, "_visible_buttons", fake_visible_buttons)
    monkeypatch.setattr(cc, "_click_known_accept", fake_click_known_accept)
    monkeypatch.setattr(cc, "_click_in_owner", fake_click_in_owner)

    result = asyncio.run(cc.handle_cookie_consent(_FakePage(), timeout_s=3.0))

    assert calls["scan"] >= 3
    assert result["cookie_consent_handled"] == "YES"
    assert clicked["button"]["text"] == "Tout accepter"


def test_handle_cookie_consent_still_blocks_if_never_found(monkeypatch):
    """Fail-closed unchanged: polling longer must never turn into a bypass
    -- a banner with genuinely no standard button still raises ConsentBlocked."""
    async def fake_username_visible(page):
        return True

    async def fake_consent_ui_visible(page):
        return True

    async def fake_visible_buttons(owner):
        return []  # never renders anything, ever

    async def fake_click_known_accept(page):
        return None

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_consent_ui_visible", fake_consent_ui_visible)
    monkeypatch.setattr(cc, "_visible_buttons", fake_visible_buttons)
    monkeypatch.setattr(cc, "_click_known_accept", fake_click_known_accept)

    try:
        asyncio.run(cc.handle_cookie_consent(_FakePage(), timeout_s=1.0))
        raised = False
    except cc.ConsentBlocked:
        raised = True
    assert raised is True
