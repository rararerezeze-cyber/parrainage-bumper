"""tools/inspect_code_parrainage_edit_mechanism.py — structural read-only
guarantees. The live browser section is exercised for real only via an
explicit operator-authorized GitHub Actions run, not here.
"""
from __future__ import annotations

import inspect

import tools.inspect_code_parrainage_edit_mechanism as mod


def test_module_never_calls_fill_or_click_or_save():
    """No source anywhere in this module may fill a field, click anything,
    or reference the real writer's fill/save helpers -- this script only
    logs in, navigates, and reads (page.goto / page.evaluate / dump/fetch).
    """
    src = inspect.getsource(mod)
    for forbidden in (".fill(", ".click(", "human_click", "_fill_and_save", "_click_save"):
        assert forbidden not in src, f"forbidden call found: {forbidden!r}"


def test_module_does_not_import_fill_and_save():
    from platforms.code_parrainage import writer as real_writer

    assert not hasattr(mod, "_fill_and_save")
    assert not hasattr(mod, "execute_write")
    # Sanity: the real writer DOES have it (proves this is a meaningful
    # absence, not just a typo in the assertion).
    assert hasattr(real_writer, "_fill_and_save")


def test_dump_form_debug_reused_is_read_only():
    """Reuses the same read-only DOM census helper already proven safe for
    parrainage-co's inspect_only path -- no .fill()/.click()/dispatchEvent
    in its injected script."""
    src = inspect.getsource(mod._dump_form_debug)
    for forbidden in (".fill(", ".click(", "dispatchEvent", "submit()"):
        assert forbidden not in src


def test_discover_all_candidates_only_navigates_and_reads():
    src = inspect.getsource(mod._discover_all_candidates)
    assert "page.goto(" in src
    assert "page.evaluate(" in src
    for forbidden in (".fill(", ".click("):
        assert forbidden not in src


def test_save_like_button_labels_are_flagged_not_clicked():
    """The chosen_url_has_save_button / looks_like_public_view checks must
    only ever READ button labels (from the dump) -- never act on them."""
    src = inspect.getsource(mod.main)
    idx = src.index("chosen_url_has_save_button")
    span = src[idx : idx + 600]
    assert ".click(" not in span
    assert "human_click" not in span
