"""Repository-only execution tests: stale baselines never trigger repeat saves."""
import asyncio
from types import SimpleNamespace

import pytest

from platforms.oneparrainage import writer as one
from platforms.parrainage_co import writer as pc
from tools.run_verified_writers import _scope_plan_to_field


async def noop(*args, **kwargs):
    pass


def fake_browser(monkeypatch, writer):
    page = SimpleNamespace(goto=noop, screenshot=noop, close=noop, set_viewport_size=noop)

    async def new_page():
        return page

    ctx = SimpleNamespace(new_page=new_page, close=noop)
    browser = SimpleNamespace(close=noop)

    async def launch(**kwargs):
        return browser

    async def new_context(_browser):
        return ctx

    class PW:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: PW())
    monkeypatch.setattr(writer, "_bumper", lambda: SimpleNamespace(
        human_sleep=noop, new_context=new_context,
        CONFIG={"parrainage": {"url": "https://example.test"}},
    ))
    monkeypatch.setattr(writer.asyncio, "sleep", noop)


@pytest.mark.parametrize("program", ["igraal", "totalenergies"])
def test_oneparrainage_already_current_rechecks_account_public_without_save(monkeypatch, program):
    plan, note = _scope_plan_to_field(one.build_write_plan("1parrainage", program, "fr"), "referee_reward")
    assert note is None
    body = '<p>' + one._named_entity_encode(plan.variables["referee_reward"]) + '</p>'
    body += '<p>operator-personal-link-and-code-preserved</p>'
    fake_browser(monkeypatch, one)
    monkeypatch.setattr(one, "content_write_allowed", lambda *_: True)
    monkeypatch.setattr(one, "_cfg", lambda: {})
    monkeypatch.setattr(one, "_login", noop)
    monkeypatch.setattr(one, "_detect_challenge", noop)

    async def ready(*args):
        return True

    async def get(*args):
        return body

    async def forbidden_save(*args, **kwargs):
        pytest.fail("already-current content attempted another save")

    monkeypatch.setattr(one, "_ck_ready", ready)
    monkeypatch.setattr(one, "_ck_get", get)
    monkeypatch.setattr(one, "_fill_and_save", forbidden_save)
    monkeypatch.setattr(one, "_public_detail_for_offer_id", lambda *_: body)
    result = asyncio.run(one.execute_write(plan, dry_run=False))
    assert result.ok and result.post_match
    assert result.writes_performed == 0
    assert result.evidence_checks["submit_ok"] is False
    assert result.account_reread_text == body


def test_parrainage_current_igraal_rechecks_public_without_save(monkeypatch):
    plan, note = _scope_plan_to_field(pc.build_write_plan("parrainage-co", "igraal", "fr"), "referee_reward")
    assert note is None
    fake_browser(monkeypatch, pc)
    monkeypatch.setattr(pc, "content_write_allowed", lambda *_: True)
    monkeypatch.setattr(pc, "_login", noop)

    async def edit(*args):
        return "https://example.test/account/edit"

    async def reread(*args):
        return plan.rendered

    async def forbidden_save(*args, **kwargs):
        pytest.fail("already-current content attempted another save")

    monkeypatch.setattr(pc, "_resolve_edit_url", edit)
    monkeypatch.setattr(pc, "_reread_account_fields", reread)
    monkeypatch.setattr(pc, "_fill_and_save", forbidden_save)
    monkeypatch.setattr(pc, "fetch_text", lambda *_: '<blockquote class="offer-quote">' + plan.rendered.replace('\n', '<br>') + '</blockquote>')
    result = asyncio.run(pc.execute_write(plan, dry_run=False))
    assert result.ok and result.post_match
    assert result.writes_performed == 0
    assert result.evidence_checks["submit_ok"] is False
