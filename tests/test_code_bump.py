import asyncio

import pytest

from lib.code_bump import BATCH_BUTTONS, BatchRefreshBlocked, bump_listings


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


class NoButtons:
    async def count(self):
        return 0


class Page:
    url = "https://code-parrainage.net/moncompte"

    def __init__(self, keys):
        self.initial = [Button(key) for key in keys]
        self.buttons = list(self.initial)
        self.clicks = []
        self.reloads = 0
        self.started = 0

    def locator(self, selector):
        if selector == BATCH_BUTTONS:
            return NoButtons()
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


class BatchButton(Button):
    def __init__(self, page, key, visible=True, enabled=True):
        super().__init__(key)
        self.page = page
        self.visible = visible
        self.enabled = enabled

    async def get_attribute(self, name):
        return {"data-compteur": str(self.page.used),
                "data-annonces": str(self.page.listings)}.get(name)

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled


class BatchPage(Page):
    def __init__(self, used=2, visibility=(True, False), server_success=True):
        self.used = used
        self.listings = 34
        self.server_success = server_success
        self.buttons = [BatchButton(self, f"copy-{i}", shown)
                        for i, shown in enumerate(visibility)]
        self.clicks = []
        self.reloads = 0
        self.started = 0

    async def click(self, _page, btn):
        self.clicks.append(btn.key)

    async def goto(self, url, **kwargs):
        self.reloads += 1
        if self.server_success:
            self.used += 1

    def locator(self, selector):
        assert selector == BATCH_BUTTONS, "global controls must never fall through to individual clicks"
        return Buttons(self)


@pytest.mark.parametrize("visibility", [(True, False), (False, True), (True, True)])
def test_global_button_copies_are_one_action_for_all_34_listings(visibility):
    page = BatchPage(visibility=visibility)
    assert run(page) == 34
    assert len(page.clicks) == 1
    assert page.used == 3
    assert page.reloads == 1
    assert page.started == 1


def test_global_click_without_server_counter_change_is_not_reported_successful_or_replayed():
    page = BatchPage(server_success=False)
    with pytest.raises(RuntimeError, match="compteur serveur non confirme"):
        run(page)
    assert len(page.clicks) == 1
    assert page.started == 1
    assert page.reloads == 1


@pytest.mark.parametrize("guard", ["quota", "hidden", "disabled", "missing", "inconsistent"])
def test_batch_guards_fail_closed_before_any_click(guard):
    page = BatchPage()
    if guard == "quota":
        page.used = 5
    elif guard == "hidden":
        for button in page.buttons:
            button.visible = False
    elif guard == "disabled":
        for button in page.buttons:
            button.enabled = False
    elif guard == "missing":
        async def missing(name):
            return None
        page.buttons[0].get_attribute = missing
    else:
        async def inconsistent(name):
            return "1" if name == "data-compteur" else "34"
        page.buttons[0].get_attribute = inconsistent
    with pytest.raises(BatchRefreshBlocked):
        run(page)
    assert page.clicks == []
    assert page.started == 0


def test_global_uncertain_click_is_single_shot():
    page = BatchPage()

    async def uncertain(_page, button):
        page.clicks.append(button.key)
        raise RuntimeError("submission timeout")

    with pytest.raises(RuntimeError, match="submission timeout"):
        run(page, uncertain)
    assert len(page.clicks) == 1
    assert page.reloads == 0
    assert page.started == 1
