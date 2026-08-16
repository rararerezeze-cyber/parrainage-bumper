"""inspect_only gating for parrainage-co and code-parrainage writers.

Two things this must prove, without needing a full Playwright mock:

1. inspect_only=True bypasses the "no changed_fields -> NOOP" short-circuit
   (Kraken is currently NO_SAFE_DIFF on both platforms -- without this, a
   real inspection could never reach a live login at all today).
2. inspect_only=False keeps the exact old NOOP behavior (never attempts a
   live session when there's nothing to write) -- the restructured gating
   must not change non-inspect_only behavior.
3. The inspect_only branch's `return` is textually BEFORE the
   `_fill_and_save(` call in execute_write's source -- straight-line async
   function, no loops/gotos back into it -- so inspect_only structurally
   cannot reach fill/save.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


class _MarkerReached(Exception):
    """Raised by a stubbed async_playwright() to prove execution reached
    the live-session section, without actually launching a browser."""


def _fake_async_playwright_that_raises(monkeypatch, module_path: str) -> None:
    class _FakePW:
        async def __aenter__(self):
            raise _MarkerReached("reached live session section")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: _FakePW())


def _plan_no_diff(platform: str):
    if platform == "parrainage-co":
        from platforms.parrainage_co.writer import WritePlan
    else:
        from platforms.code_parrainage.writer import WritePlan
    return WritePlan(
        platform=platform,
        program="kraken",
        language="fr",
        announcement_url="https://example.test/kraken",
        edit_url="https://example.test/edit/kraken",
        historical="x",
        rendered="x",
        variables={},
        platform_values={},
        changed_fields={},  # NO_SAFE_DIFF -- matches real current Kraken state
        structure_preserved=True,
        mutable_fields=[],
    )


@pytest.mark.parametrize("platform", ["parrainage-co", "code-parrainage"])
def test_non_inspect_only_still_noops_without_a_live_session(monkeypatch, platform):
    _fake_async_playwright_that_raises(monkeypatch, platform)
    if platform == "parrainage-co":
        from platforms.parrainage_co.writer import execute_write
    else:
        from platforms.code_parrainage.writer import execute_write

    plan = _plan_no_diff(platform)
    result = asyncio.run(execute_write(plan, dry_run=False, inspect_only=False))
    assert result.error == "NO_SAFE_DIFF"
    assert result.steps == ["noop"]


@pytest.mark.parametrize("platform", ["parrainage-co", "code-parrainage"])
def test_inspect_only_bypasses_noop_and_reaches_live_session(monkeypatch, platform):
    _fake_async_playwright_that_raises(monkeypatch, platform)
    if platform == "parrainage-co":
        from platforms.parrainage_co.writer import execute_write
    else:
        from platforms.code_parrainage.writer import execute_write

    plan = _plan_no_diff(platform)
    # Reaching _MarkerReached (not "noop") proves inspect_only really
    # bypassed the changed_fields short-circuit and attempted a real
    # session, instead of silently doing nothing.
    with pytest.raises(_MarkerReached):
        asyncio.run(execute_write(plan, dry_run=False, inspect_only=True))


@pytest.mark.parametrize(
    "module_path",
    ["platforms.parrainage_co.writer", "platforms.code_parrainage.writer"],
)
def test_inspect_only_return_precedes_fill_and_save_in_source(module_path):
    import importlib

    mod = importlib.import_module(module_path)
    src = inspect.getsource(mod.execute_write)
    idx_inspect = src.index("if inspect_only:")
    idx_return = src.index("return WriteResult(", idx_inspect)
    idx_fill = src.index("_fill_and_save(")
    assert idx_inspect < idx_return < idx_fill, (
        "inspect_only's return must appear before the fill/save call — "
        "otherwise inspect_only could fall through into a real write"
    )


@pytest.mark.parametrize(
    "module_path",
    ["platforms.parrainage_co.writer", "platforms.code_parrainage.writer"],
)
def test_dump_form_debug_never_calls_fill_or_click(module_path):
    """Static guard: _dump_form_debug's page.evaluate script must only read
    (querySelectorAll / offsetWidth / value / innerText), never .fill(),
    .click(), or dispatch a submit-like event."""
    import importlib

    mod = importlib.import_module(module_path)
    src = inspect.getsource(mod._dump_form_debug)
    for forbidden in (".fill(", ".click(", "dispatchEvent", "submit()"):
        assert forbidden not in src
