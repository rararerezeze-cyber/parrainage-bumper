"""tools/diagnose_code_parrainage_offre_save.py — structural proof this
diagnostic can never submit/save. The live browser session only runs via
an explicit operator-authorized GitHub Actions dispatch, not here.
"""
from __future__ import annotations

import inspect

import tools.diagnose_code_parrainage_offre_save as mod


def test_module_never_clicks_anything():
    src = inspect.getsource(mod)
    assert ".click(" not in src
    assert "human_click" not in src


def test_module_never_calls_slider_solver_or_save_helpers():
    src = inspect.getsource(mod)
    assert "solve_slider" not in src
    assert "_click_save" not in src


def test_only_fill_and_keystroke_locators_touch_offre():
    src = inspect.getsource(mod)
    assert 'locator("textarea#offre").fill(' in src
    assert "press_sequentially(" in src


def test_reuses_tested_canary_render_helper():
    assert mod._canary_and_original_renders is not None


def test_reloads_after_each_fill_test_before_any_further_action():
    src = inspect.getsource(mod.main)
    idx_fill1 = src.index('locator("textarea#offre").fill(rendered_canary)')
    idx_reload1 = src.index("page.goto(EDIT_URL", idx_fill1)
    idx_keystrokes = src.index("press_sequentially(", idx_reload1)
    idx_reload2 = src.index("page.goto(EDIT_URL", idx_keystrokes)
    assert idx_fill1 < idx_reload1 < idx_keystrokes < idx_reload2
