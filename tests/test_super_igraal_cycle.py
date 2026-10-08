"""Exercise the corrected resource with repository-only browser doubles."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from lib.content_plan import build_plan
from lib.super_parrain_post_verify import verify_public_program
from platforms.super_parrain import igraal_cycle as cycle


class Message:
    def __init__(self, text):
        self.value = text
        self.fills = 0

    async def count(self):
        return 1

    async def input_value(self):
        return self.value

    async def fill(self, text):
        self.value = text
        self.fills += 1


class Announcement:
    def __init__(self, text):
        self.message = Message(text)
        self.url = ""
        self.saves = 0
        self.closed = False
        self.page_text = "iGraal announcement"

    async def goto(self, url, **kwargs):
        self.url = url

    def locator(self, selector):
        if selector == 'textarea[name="form[message]"]':
            return self.message
        assert selector == "input, textarea"
        return self

    async def evaluate_all(self, script):
        return ["form[message]", "form[code]"]

    async def inner_text(self, selector):
        return self.page_text

    async def close(self):
        self.closed = True


def setup(monkeypatch, *, already_current=False):
    plan = cycle.build_write_plan("super-parrain", "igraal", "fr", only_fields=["referee_reward"])
    # A normal verified cycle updates the production baseline to 3 EUR. The
    # regression fixture must retain the old 5 EUR case independently of that
    # mutable repository state, without rewriting any production data.
    new_reward = plan.variables["referee_reward"]
    old_reward = new_reward.replace("3 €", "5 €", 1)
    assert old_reward != new_reward
    plan = replace(plan, historical=plan.rendered.replace(new_reward, old_reward),
                   platform_values={**plan.platform_values, "referee_reward": old_reward},
                   changed_fields={"referee_reward": {"old": old_reward, "new": new_reward}})
    monkeypatch.setattr(cycle, "build_write_plan", lambda *a, **k: plan)
    assert set(plan.changed_fields) == {"referee_reward"}
    content = Announcement(plan.rendered if already_current else plan.historical)
    opened = []
    persisted = []

    async def new_page():
        opened.append(content)
        return content

    page = SimpleNamespace(context=SimpleNamespace(new_page=new_page))
    monkeypatch.setattr(cycle, "load_plan", lambda: build_plan(["igraal"]))
    monkeypatch.setattr(cycle, "live_writes_enabled", lambda *_: True)
    monkeypatch.setattr(cycle, "should_prefill_content", lambda *_: (True, "canary"))
    monkeypatch.setattr(cycle, "is_eligible", lambda: (True, None, 0))
    monkeypatch.setattr(cycle, "is_historical_bumper_authorized", lambda: True)

    async def save(target):
        assert target is content
        target.saves += 1

    monkeypatch.setattr(cycle, "_click_save_not_boost", save)

    def verify(*args, **kwargs):
        return verify_public_program(*args, published_override=content.message.value, fetch=False, **kwargs)

    monkeypatch.setattr(cycle, "verify_public_program", verify)
    monkeypatch.setattr("tools.run_verified_writers._persist_verified_baseline",
                        lambda p, result: persisted.append((p, result)))
    return page, content, plan, opened, persisted


def test_correct_announcement_is_saved_once_and_personal_fields_are_preserved(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch)
    result = asyncio.run(cycle.update_announcement(page, "https://www.super-parrain.com/tableau-de-bord/codes-promo/igraal/edit"))
    assert result["content_post_verify"]["post_match"] is True
    assert result["content_post_verify"]["immutable_ok"] is True
    assert content.saves == 1
    assert content.message.fills == 1
    assert "/annonces/adrien-b-8/edit" in content.url
    assert "codes-promo" not in content.url
    assert result["writes_performed"] == 1
    assert content.message.value == plan.historical.replace(
        plan.platform_values["referee_reward"], plan.variables["referee_reward"],
    )
    assert content.closed
    assert len(persisted) == 1


@pytest.mark.parametrize("block", ["plan", "cooldown", "authorization", "challenge", "structure"])
def test_fail_closed_guards_never_fill_or_save(monkeypatch, block):
    page, content, plan, opened, persisted = setup(monkeypatch)
    if block == "plan":
        monkeypatch.setattr(cycle, "load_plan", lambda: build_plan([]))
    elif block == "cooldown":
        monkeypatch.setattr(cycle, "is_eligible", lambda: (False, None, 1))
    elif block == "authorization":
        monkeypatch.setattr(cycle, "is_historical_bumper_authorized", lambda: False)
    elif block == "challenge":
        content.page_text = "CAPTCHA required"
    else:
        content.message.value = "operator-edited body must be preserved"
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert result["skipped"] is True
    assert content.saves == 0
    assert content.message.fills == 0
    assert persisted == []
    if block in {"plan", "cooldown", "authorization"}:
        assert opened == []


def test_uncertain_save_is_not_replayed_and_is_reported_unknown(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch)

    async def uncertain(target):
        target.saves += 1
        raise RuntimeError("timeout after save")

    monkeypatch.setattr(cycle, "_click_save_not_boost", uncertain)
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert content.saves == 1
    assert result["content_post_verify"]["post_match"] is False
    assert result["writes_performed"] is None
    assert persisted == []
    assert content.closed


def test_already_current_message_is_reconciled_without_another_save(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch, already_current=True)
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert result["content_post_verify"]["post_match"] is True
    assert result["writes_performed"] == 0
    assert result["fields_filled"] == []
    assert content.saves == 0
    assert content.message.fills == 0
    assert persisted[0][1].writes_performed == 0


def test_redirect_to_codes_promo_cannot_receive_announcement_text(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch)

    async def wrong(url, **kwargs):
        content.url = "https://www.super-parrain.com/tableau-de-bord/codes-promo/igraal/edit"

    content.goto = wrong
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert result["content_post_verify"]["post_match"] is False
    assert content.message.fills == 0
    assert content.saves == 0
    assert persisted == []


def test_failed_public_verification_cannot_advance_baseline(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch)
    monkeypatch.setattr(cycle, "verify_public_program", lambda *a, **k:
                        SimpleNamespace(post_match=False, to_dict=lambda: {"post_match": False}))
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert content.saves == 1
    assert result["writes_performed"] == 1
    assert result["content_post_verify"]["post_match"] is False
    assert persisted == []


def test_plan_failure_is_visible_without_exposing_exception_content(monkeypatch):
    page, content, plan, opened, persisted = setup(monkeypatch)

    def invalid(*a, **k):
        raise ValueError("private form value")

    monkeypatch.setattr(cycle, "build_write_plan", invalid)
    result = asyncio.run(cycle.update_announcement(page, "codes-promo/igraal/edit"))
    assert result["content_post_verify"]["post_match"] is False
    assert "private" not in str(result)
    assert opened == []
    assert persisted == []
