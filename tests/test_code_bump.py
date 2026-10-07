import asyncio

import pytest

from lib.code_bump import bump_listings


class Button:
    def __init__(self, key):
        self.key = key

    async def evaluate(self, _script):
        return self.key

    async def wait_for(self, **kwargs):
        pass

    async def is_enabled(self):
        return True

    async def scroll_into_view_if_needed(self):
        pass


class Buttons:
    def __init__(self, page):
        self.page = page

    async def count(self):
        return len(self.page.buttons)

    def nth(self, index):
        return self.page.buttons[index]


class Page:
    url = "https://code-parrainage.net/moncompte"

    def __init__(self, keys):
        self.initial = [Button(key) for key in keys]
        self.buttons = list(self.initial)
        self.clicks = []
        self.reloads = 0
        self.started = 0

    def locator(self, _selector):
        return Buttons(self)

    async def goto(self, url, **kwargs):
        self.reloads += 1
        # Changed DOM order, already clicked controls are gone.
        self.buttons = [b for b in reversed(self.initial) if b.key not in self.clicks]

    async def click(self, _page, btn):
        assert btn.key not in self.clicks
        self.clicks.append(btn.key)
        # Reproduce the live regression: all remaining locators disappear.
        self.buttons = []

    async def pause(self, *_args):
        pass

    def mark_started(self):
        self.started += 1


def run(page, click=None):
    return asyncio.run(bump_listings(
        page, page.url, click=click or page.click, pause=page.pause,
        mark_started=page.mark_started,
    ))


def test_two_listings_survive_dom_disappearance_and_reordering():
    page = Page([["href", "/actualiser/1"], ["href", "/actualiser/2"]])
    assert run(page) == 2
    assert page.clicks == [["href", "/actualiser/1"], ["href", "/actualiser/2"]]
    assert page.reloads == 1
    assert page.started == 2


@pytest.mark.parametrize("keys", [[None], [["id", "same"], ["id", "same"]]])
def test_missing_or_duplicate_identity_aborts_before_any_action(keys):
    page = Page(keys)
    with pytest.raises(RuntimeError, match="identite stable"):
        run(page)
    assert page.clicks == []
    assert page.started == 0


def test_uncertain_click_is_never_retried_or_followed_by_another_listing():
    page = Page([["id", "a"], ["id", "b"]])

    async def uncertain(_page, btn):
        page.clicks.append(btn.key)
        raise RuntimeError("timeout after submission")

    with pytest.raises(RuntimeError, match="timeout"):
        run(page, uncertain)
    assert page.clicks == [["id", "a"]]
    assert page.reloads == 0
    assert page.started == 1
