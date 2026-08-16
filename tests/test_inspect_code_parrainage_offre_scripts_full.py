"""tools/inspect_code_parrainage_offre_scripts_full.py — structural proof
this diagnostic can never submit/save. Live browser session only runs via
an explicit operator-authorized GitHub Actions dispatch, not here.
"""
from __future__ import annotations

import inspect

import tools.inspect_code_parrainage_offre_scripts_full as mod


def test_module_never_clicks_or_submits_anything():
    src = inspect.getsource(mod.main)
    assert ".click(" not in src
    assert "human_click" not in src
    assert ".submit(" not in src
    assert "requestSubmit" not in src


def test_module_never_calls_slider_solver_or_save_helpers():
    src = inspect.getsource(mod.main)
    assert "solve_slider" not in src
    assert "_click_save" not in src


def test_dumps_full_script_text_not_just_excerpts():
    src = inspect.getsource(mod.main)
    assert "textContent" in src
    assert "write_text(text, encoding" in src
