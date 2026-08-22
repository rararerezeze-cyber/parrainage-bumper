"""Cookie-consent label classification — no network."""
import asyncio
from pathlib import Path

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


def test_consent_owners_do_not_scan_main_frame_twice():
    main = object()
    child = object()
    page = _FakePage()
    page.main_frame = main
    page.frames = [main, child]

    assert cc._consent_owners(page) == [page, child]


def test_handle_cookie_consent_polls_for_late_rendering_button(monkeypatch):
    """Regression: keep polling while a banner is visible but no accept
    button has rendered yet, instead of giving up after one fixed-delay
    check gated on the login field's own visibility.

    Evidence: 1parrainage GH Actions DOM census (data/captures/
    1parrainage-edit-map.json, 2026-08-15) showed input#_username
    visible=1 from the very first census -- alongside a Sirdata
    ConsentFramework banner (sd-cmp-* nodes) whose Accept button had not rendered
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


def test_consent_absent_takes_no_action(monkeypatch):
    clicked = {"value": False}

    async def fake_username_visible(page):
        return True

    async def fake_scan(page):
        return {"banner": False, "accept_candidates": [], "settings_or_reject": []}

    async def fake_known(page):
        clicked["value"] = True
        return "unexpected"

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_scan_consent_ui", fake_scan)
    monkeypatch.setattr(cc, "_click_known_accept", fake_known)

    result = asyncio.run(cc.handle_cookie_consent(_FakePage(), timeout_s=0.1))

    assert result["cookie_consent_handled"] == "NO"
    assert result["reason"] == "no_visible_consent_banner"
    assert clicked["value"] is False


def test_iframe_consent_uses_the_candidate_owner(monkeypatch):
    frame = object()
    page = _FakePage()
    page.frames = [frame]
    state = {"clicked": False, "owner": None}
    button = {"text": "Tout accepter", "id": "", "visible": True}

    async def fake_username_visible(page):
        return True

    async def fake_scan(page):
        return {
            "banner": not state["clicked"],
            "accept_candidates": [] if state["clicked"] else [(frame, button)],
            "settings_or_reject": [],
        }

    async def fake_known(page):
        return None

    async def fake_click(owner, picked):
        state["owner"] = owner
        state["clicked"] = True
        return True

    async def fake_banner(page):
        return not state["clicked"]

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_scan_consent_ui", fake_scan)
    monkeypatch.setattr(cc, "_click_known_accept", fake_known)
    monkeypatch.setattr(cc, "_click_in_owner", fake_click)
    monkeypatch.setattr(cc, "_consent_ui_visible", fake_banner)

    result = asyncio.run(cc.handle_cookie_consent(page, timeout_s=0.1))

    assert result["cookie_consent_handled"] == "YES"
    assert state["owner"] is frame


def test_ambiguous_accept_ui_fails_closed_without_click(monkeypatch):
    clicked = {"value": False}
    buttons = [
        (object(), {"text": "Tout accepter", "id": "a", "visible": True}),
        (object(), {"text": "Accept all", "id": "b", "visible": True}),
    ]

    async def fake_username_visible(page):
        return True

    async def fake_scan(page):
        return {"banner": True, "accept_candidates": buttons, "settings_or_reject": []}

    async def fake_click(owner, picked):
        clicked["value"] = True
        return True

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_scan_consent_ui", fake_scan)
    monkeypatch.setattr(cc, "_click_in_owner", fake_click)

    try:
        asyncio.run(cc.handle_cookie_consent(_FakePage(), timeout_s=0.1))
        raised = False
    except cc.ConsentBlocked as exc:
        raised = "ambiguous consent buttons" in str(exc)

    assert raised is True
    assert clicked["value"] is False


def test_consent_helper_has_strict_sirdata_target_and_no_save_path():
    src = Path(cc.__file__).read_text(encoding="utf-8")
    assert '#sd-cmp button:has-text("Tout accepter")' in src
    assert '#sd-cmp [role="dialog"]:has(#sd-cmp-title-ccpa)' in src
    assert '[role="button"][title="Close"]' in src
    for forbidden in ("parrainages/edit", "Envoyer", "_click_save", "CKEDITOR"):
        assert forbidden not in src


def test_login_visibility_supports_rctv_email_form_without_weakening_cmp_targets():
    src = Path(cc.__file__).read_text(encoding="utf-8")
    assert "input[type='email']" in src
    assert "input[name='email']" in src


def test_known_sirdata_ccpa_continue_uses_only_exact_dialog_control():
    state = {"native_clicks": 0, "selectors": []}

    class Control:
        first = None

        def __init__(self):
            self.first = self

        async def count(self):
            return 1

        async def evaluate(self, script):
            assert script == "(el) => el.click()"
            state["native_clicks"] += 1

    class Dialog:
        first = None

        def __init__(self):
            self.first = self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        def locator(self, selector):
            state["selectors"].append(selector)
            return Control()

    class Page(_FakePage):
        def locator(self, selector):
            state["selectors"].append(selector)
            return Dialog()

    result = asyncio.run(cc._click_known_continue(Page()))

    assert result == "Close"
    assert state == {
        "native_clicks": 1,
        "selectors": [cc.SIRDATA_CCPA_DIALOG, cc.SIRDATA_CCPA_CLOSE],
    }


def test_sirdata_ccpa_ambiguous_close_controls_fail_closed():
    class Controls:
        async def count(self):
            return 2

    class Dialog:
        first = None

        def __init__(self):
            self.first = self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        def locator(self, selector):
            assert selector == cc.SIRDATA_CCPA_CLOSE
            return Controls()

    class Page(_FakePage):
        def locator(self, selector):
            assert selector == cc.SIRDATA_CCPA_DIALOG
            return Dialog()

    try:
        asyncio.run(cc._click_known_continue(Page()))
        raised = False
    except cc.ConsentBlocked as exc:
        raised = "ambiguous Sirdata CCPA Close controls: 2" in str(exc)
    assert raised is True


def test_handle_cookie_consent_accepts_exact_sirdata_ccpa_continue(monkeypatch):
    state = {"closed": False}

    async def fake_username_visible(page):
        return True

    async def fake_scan(page):
        return {
            "banner": not state["closed"],
            "accept_candidates": [],
            "settings_or_reject": [],
        }

    async def fake_known_accept(page):
        return None

    async def fake_continue(page):
        state["closed"] = True
        return "Close"

    async def fake_banner(page):
        return not state["closed"]

    monkeypatch.setattr(cc, "_username_visible", fake_username_visible)
    monkeypatch.setattr(cc, "_scan_consent_ui", fake_scan)
    monkeypatch.setattr(cc, "_click_known_accept", fake_known_accept)
    monkeypatch.setattr(cc, "_click_known_continue", fake_continue)
    monkeypatch.setattr(cc, "_consent_ui_visible", fake_banner)

    result = asyncio.run(cc.handle_cookie_consent(_FakePage(), timeout_s=0.1))

    assert result == {
        "cookie_consent_handled": "YES",
        "button": "Close",
        "login_form_visible": True,
        "overlay_gone": True,
        "via": "known_sirdata_ccpa_continue",
    }
