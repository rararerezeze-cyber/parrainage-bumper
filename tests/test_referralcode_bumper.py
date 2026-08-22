from __future__ import annotations

import asyncio

import bumper


class _Locator:
    def __init__(self, selector: str):
        self.selector = selector

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def scroll_into_view_if_needed(self):
        return None


class _Page:
    def __init__(self):
        self.url = "about:blank"
        self.goto_calls = []

    async def goto(self, url: str, **kwargs):
        self.goto_calls.append((url, kwargs))
        if url.endswith("/login/") and kwargs.get("wait_until") == "networkidle":
            raise TimeoutError("background requests never became idle")
        self.url = url

    async def screenshot(self, **_kwargs):
        return None

    def locator(self, selector: str):
        return _Locator(selector)

    async def wait_for_url(self, predicate, **_kwargs):
        assert predicate(self.url)

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def inner_text(self, _selector: str):
        return "can click 0"

    async def close(self):
        return None


class _Context:
    def __init__(self):
        self.page = _Page()

    async def new_page(self):
        return self.page

    async def close(self):
        return None


def test_referralcode_login_does_not_require_network_idle(monkeypatch):
    """A usable login DOM must not fail on long-lived background requests."""
    context = _Context()

    async def fake_new_context(_browser):
        return context

    async def fake_sleep(*_args, **_kwargs):
        return None

    async def fake_fill(*_args, **_kwargs):
        return True

    async def fake_click(page, locator):
        if "SIGN IN" in locator.selector:
            page.url = "https://www.referralcode.tv/my-account/"

    monkeypatch.setattr(bumper, "new_context", fake_new_context)
    monkeypatch.setattr(bumper, "human_sleep", fake_sleep)
    monkeypatch.setattr(bumper, "smart_fill", fake_fill)
    monkeypatch.setattr(bumper, "human_click", fake_click)

    asyncio.run(bumper.run_referralcode(object()))

    login_url, login_kwargs = context.page.goto_calls[0]
    assert login_url == "https://www.referralcode.tv/login/"
    assert login_kwargs == {"wait_until": "domcontentloaded", "timeout": 45000}
