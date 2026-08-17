"""tools/canary_write_code_parrainage.py — pre-browser logic + structural
safety guarantees. The live browser section is exercised for real only via
explicit operator-authorized GitHub Actions runs, not here.
"""
from __future__ import annotations

import inspect

import pytest

from lib.operator_overrides import OperatorOverrideStore
from tools.canary_write_code_parrainage import (
    CANARY_VALUE,
    EXPECTED_CODE_OU_LIEN,
    EXPECTED_COMPANY,
    FIELD,
    ORIGINAL_VALUE,
    PLATFORM,
    PROGRAM,
    _account_snapshot,
    _canary_and_original_renders,
    _guard_identity,
)


# --- pre-browser render computation ----------------------------------------

def test_canary_and_original_renders_differ_only_in_reward():
    rendered_canary, rendered_original, var_canary, var_original = (
        _canary_and_original_renders()
    )
    assert var_canary["referee_reward"] == CANARY_VALUE
    assert var_original["referee_reward"] == ORIGINAL_VALUE
    assert var_canary["personal_code"] == var_original["personal_code"]
    assert var_canary["personal_link"] == var_original["personal_link"]
    assert CANARY_VALUE in rendered_canary
    assert ORIGINAL_VALUE in rendered_original
    assert ORIGINAL_VALUE not in rendered_canary
    assert CANARY_VALUE not in rendered_original


def test_override_store_file_is_byte_identical_after_render_computation():
    from lib.paths import OPERATOR_OVERRIDES_PATH

    before = OPERATOR_OVERRIDES_PATH.read_bytes()
    _canary_and_original_renders()
    after = OPERATOR_OVERRIDES_PATH.read_bytes()
    assert after == before


def test_refuses_when_a_platform_override_already_exists():
    store = OperatorOverrideStore()
    store.upsert(PROGRAM, FIELD, "some other value", platform=PLATFORM, message="pre-existing")
    try:
        with pytest.raises(RuntimeError, match="already exists"):
            _canary_and_original_renders()
    finally:
        store.remove(PROGRAM, FIELD, platform=PLATFORM)


# --- identity guard (CRITICAL FAIL) -----------------------------------------

def test_guard_identity_passes_on_expected_values():
    report: dict = {}
    _guard_identity(
        {"company": EXPECTED_COMPANY, "code_ou_lien": EXPECTED_CODE_OU_LIEN},
        phase="before",
        report=report,
    )
    assert report["identity_checks"]["before"]["ok"] is True
    assert "critical_fail" not in report


def test_guard_identity_raises_on_company_drift():
    report: dict = {}
    with pytest.raises(RuntimeError, match="IDENTITY DRIFT"):
        _guard_identity(
            {"company": "SomeoneElse", "code_ou_lien": EXPECTED_CODE_OU_LIEN},
            phase="canary",
            report=report,
        )
    assert "critical_fail" in report
    assert "SomeoneElse" in report["critical_fail"]


def test_guard_identity_raises_on_code_ou_lien_drift():
    report: dict = {}
    with pytest.raises(RuntimeError, match="IDENTITY DRIFT"):
        _guard_identity(
            {"company": EXPECTED_COMPANY, "code_ou_lien": "WRONGCODE"},
            phase="rollback",
            report=report,
        )
    assert "critical_fail" in report


def test_account_snapshot_extracts_all_four_fields():
    dump = {
        "inputs": [
            {"name": "company", "preview": "Kraken"},
            {"name": "code_ou_lien", "preview": "cpbrgddy"},
            {"name": "offre", "preview": "some text"},
            {"name": "modifpost", "preview": "84601"},
        ]
    }
    snap = _account_snapshot(dump)
    assert snap["company"] == "Kraken"
    assert snap["code_ou_lien"] == "cpbrgddy"
    assert snap["offre"] == "some text"
    assert snap["modifpost"] == "84601"
    assert snap["offre_sha256"] is not None


# --- structural guarantees on main()'s live-session state machine ---------

def test_exactly_two_save_clicks_in_source():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    assert src.count("await _click_save(") == 2, (
        "must be exactly 2 Save clicks (canary, rollback) -- no retry loop"
    )


def test_slider_check_happens_before_each_save_click():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_slider1 = src.index("_check_slider_and_solve(")
    idx_click1 = src.index("await _click_save(", idx_slider1)
    idx_slider2 = src.index("_check_slider_and_solve(", idx_click1)
    idx_click2 = src.index("await _click_save(", idx_slider2)
    assert idx_slider1 < idx_click1 < idx_slider2 < idx_click2


def test_save_button_clickability_check_happens_before_each_save_click():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_check1 = src.index("_check_save_button_clickable(")
    idx_click1 = src.index("await _click_save(", idx_check1)
    idx_check2 = src.index("_check_save_button_clickable(", idx_click1)
    idx_click2 = src.index("await _click_save(", idx_check2)
    assert idx_check1 < idx_click1 < idx_check2 < idx_click2


def test_identity_guard_called_at_least_three_times():
    """before, canary, rollback -- CRITICAL FAIL must be checked at every
    checkpoint where company/code_ou_lien could have drifted."""
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    assert src.count("_guard_identity(") >= 3


def test_rollback_reached_even_if_canary_reread_is_ambiguous():
    """MAY_HAVE_WRITTEN: the rollback save-click must not be behind an
    early return/raise gated on the canary reread's own success -- only a
    CRITICAL FAIL (identity drift) may stop the run early; an ambiguous
    canonical match must not.
    """
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_canary_click = src.index("await _click_save(page, save_btn)")
    idx_rollback_click = src.index("await _click_save(page, save_btn2)")
    span = src[idx_canary_click:idx_rollback_click]
    # The only `raise` in this span must be the identity-drift re-raise,
    # never a raise on ambiguous/false canonical match.
    assert "canonical_match_vs_expected" in span  # reread does happen
    # No early return between the two clicks.
    assert "\n    return " not in span and "\n            return " not in span


def test_no_fill_or_click_on_company_or_code_ou_lien_fields():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod)
    assert 'locator("input#company")' not in src
    assert 'locator("input#code_ou_lien")' not in src
    assert 'locator("textarea#offre")' in src  # the only field ever filled


def test_no_click_on_regles_de_redaction_or_similar_links():
    """The module's docstring/comments explain *why* these are never
    clicked (so the word itself legitimately appears in prose) -- this
    checks there is no actual selector/locator/click code referencing
    them, not that the word is absent entirely.
    """
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod)
    for forbidden in ('has-text("règles', "has-text('règles", 'has-text("J\'ai lu', "has-text('J\\'ai lu"):
        assert forbidden not in src


def test_write_verified_requires_identity_ok_and_no_critical_fail():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx = src.index("write_verified = bool(")
    idx_promo = src.index("if write_verified:")
    span = src[idx:idx_promo]
    assert "identity_ok" in span
    assert "critical_fail" in span
    assert "canary_ok" in span
    assert "rollback_ok" in span


def test_critical_fail_short_circuits_before_any_promotion_attempt():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_critical_check = src.index('if report.get("critical_fail"):')
    idx_write_verified_branch = src.index("if write_verified:")
    assert idx_critical_check < idx_write_verified_branch


# --- network instrumentation (for a FUTURE authorized run) -----------------

def test_network_listeners_registered_before_any_click():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_register = src.index("_register_network_listeners(")
    idx_click1 = src.index("await _click_save(")
    assert idx_register < idx_click1


def test_network_listeners_never_reference_cookies_or_credentials():
    """Checks the CODE only (docstring stripped) -- the docstring legitimately
    explains in prose what is never logged, which would false-positive a
    naive whole-source substring check.
    """
    import ast

    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod._register_network_listeners)
    tree = ast.parse(src)
    func = tree.body[0]
    if ast.get_docstring(func) is not None:
        func.body = func.body[1:]  # drop the docstring node
    code_only = ast.unparse(func)
    for forbidden in ("cookie", "authorization", "password", "session_id", "sessionid"):
        assert forbidden not in code_only.lower()


def test_network_listeners_only_capture_modification_php():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod._register_network_listeners)
    assert src.count("modification.php") >= 2  # request handler + response handler


def test_network_evidence_scoped_to_active_phase_only():
    """phase_ref["name"] must be None outside the two Save-click windows,
    so login and any unrelated traffic is never captured -- and must be set
    to "canary"/"rollback" only immediately around the matching click.
    """
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    assert src.count('phase_ref["name"] = "canary"') == 1
    assert src.count('phase_ref["name"] = "rollback"') == 1
    assert src.count('phase_ref["name"] = None') == 2


def test_network_evidence_included_in_final_report():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_assign = src.index('report["network_evidence"] = network_evidence')
    idx_write = src.index("REPORT_PATH.write_text(", idx_assign)
    assert idx_assign < idx_write


# --- dialog handler (root cause fix, for a FUTURE authorized run) ----------

def test_dialog_handler_registered_before_any_click():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_register = src.index("_register_dialog_handler(")
    idx_click1 = src.index("await _click_save(")
    assert idx_register < idx_click1


def test_dialog_handler_logs_before_accepting():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod._register_dialog_handler)
    idx_log = src.index('report.setdefault("dialogs_seen"')
    idx_accept = src.index("await dialog.accept()")
    assert idx_log < idx_accept


class _FakeDialog:
    def __init__(self, type_: str, message: str):
        self.type = type_
        self.message = message
        self.accepted = False
        self.dismissed = False

    async def accept(self) -> None:
        self.accepted = True

    async def dismiss(self) -> None:
        self.dismissed = True


class _FakePage:
    def __init__(self):
        self._handlers: dict = {}

    def on(self, event: str, handler) -> None:
        self._handlers[event] = handler


async def _fire_dialog(page: "_FakePage", dialog: "_FakeDialog") -> None:
    task = page._handlers["dialog"](dialog)
    await task


def _run(coro):
    import asyncio as _asyncio

    return _asyncio.run(coro)


def test_dialog_strict_accepts_expected_confirm_during_canary_phase():
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": "canary"}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("confirm", mod.EXPECTED_CONFIRM_MESSAGE)
        await _fire_dialog(page, dialog)
        return dialog, report

    dialog, report = _run(scenario())
    assert dialog.accepted is True
    assert dialog.dismissed is False
    assert "unexpected_dialog" not in report


def test_dialog_strict_accepts_expected_confirm_during_rollback_phase():
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": "rollback"}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("confirm", mod.EXPECTED_CONFIRM_MESSAGE)
        await _fire_dialog(page, dialog)
        return dialog, report

    dialog, report = _run(scenario())
    assert dialog.accepted is True
    assert dialog.dismissed is False
    assert "unexpected_dialog" not in report


def test_dialog_strict_never_accepts_expected_confirm_outside_a_save_phase():
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": None}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("confirm", mod.EXPECTED_CONFIRM_MESSAGE)
        await _fire_dialog(page, dialog)
        return dialog, report

    dialog, report = _run(scenario())
    assert dialog.accepted is False
    assert dialog.dismissed is True
    assert report["unexpected_dialog"]["in_save_phase"] is False
    assert report["unexpected_dialog"]["expected_confirm"] is True


def test_dialog_strict_never_accepts_unexpected_alert():
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": "canary"}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("alert", "Une erreur est survenue")
        await _fire_dialog(page, dialog)
        return dialog, report

    dialog, report = _run(scenario())
    assert dialog.accepted is False
    assert dialog.dismissed is True
    assert report["unexpected_dialog"]["type"] == "alert"


def test_dialog_strict_never_accepts_confirm_with_different_message():
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": "canary"}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("confirm", "Voulez-vous vraiment quitter ?")
        await _fire_dialog(page, dialog)
        return dialog, report

    dialog, report = _run(scenario())
    assert dialog.accepted is False
    assert dialog.dismissed is True
    assert report["unexpected_dialog"]["expected_confirm"] is False


def test_dialog_strict_unexpected_dialog_never_sets_identity_critical_fail():
    """unexpected_dialog must stay a distinct field from critical_fail --
    only a proven identity drift may skip the mandatory rollback, so this
    handler must never (even accidentally) write report["critical_fail"].
    """
    import tools.canary_write_code_parrainage as mod

    async def scenario():
        page, report, phase_ref = _FakePage(), {}, {"name": "canary"}
        await mod._register_dialog_handler(page, report, phase_ref)
        dialog = _FakeDialog("alert", "Une erreur est survenue")
        await _fire_dialog(page, dialog)
        return report

    report = _run(scenario())
    assert "critical_fail" not in report


def test_write_verified_also_requires_no_unexpected_dialog():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx = src.index("write_verified = bool(")
    idx_promo = src.index("if write_verified:")
    span = src[idx:idx_promo]
    assert "unexpected_dialog" in span


# --- guaranteed rollback after MAY_HAVE_WRITTEN (2026-08-17 patch) ---------

def test_rollback_attempted_even_if_canary_click_itself_raises():
    """A timeout/exception from _click_save's own wait_for_load_state must
    be caught locally (not propagate past canary_may_have_written=True)
    so execution still reaches the rollback block further down.
    """
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_may_have_written = src.index("canary_may_have_written = True")
    idx_click1 = src.index("await _click_save(page, save_btn)")
    idx_except1 = src.index("except Exception as exc:", idx_click1)
    idx_rollback_click = src.index("await _click_save(page, save_btn2)")
    assert idx_may_have_written < idx_click1 < idx_except1 < idx_rollback_click


def test_rollback_attempted_even_if_canary_reread_raises_non_identity():
    """A non-identity exception (navigation timeout, reread failure, ...)
    during the canary reread must be caught and must NOT set
    identity_failed -- only _IdentityCriticalFail may do that.
    """
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_identity_init = src.index("identity_failed = False")
    idx_except_identity = src.index("except _IdentityCriticalFail as exc:")
    idx_except_generic = src.index("except Exception as exc:", idx_except_identity)
    idx_rollback_block = src.index("if identity_failed:")
    assert idx_identity_init < idx_except_identity < idx_except_generic < idx_rollback_block


def test_only_identity_critical_fail_sets_identity_failed():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    assert src.count("identity_failed = True") == 1
    idx_except = src.index("except _IdentityCriticalFail as exc:")
    idx_set_true = src.index("identity_failed = True")
    idx_next_except = src.index("except Exception as exc:", idx_except)
    assert idx_except < idx_set_true < idx_next_except


def test_identity_failed_is_the_only_condition_that_skips_rollback():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    idx_if = src.index("if identity_failed:")
    idx_else = src.index("else:", idx_if)
    skip_block = src[idx_if:idx_else]
    assert "rollback_skipped_reason" in skip_block
    # The else branch (not identity_failed) must be the one containing the
    # actual rollback click -- i.e. reachable whenever identity has NOT failed.
    rest = src[idx_else:]
    assert "await _click_save(page, save_btn2)" in rest


def test_max_two_save_clicks_still_enforced_after_restructure():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    assert src.count("await _click_save(") == 2


def test_phase_ref_always_reset_in_finally_around_each_save_window():
    import tools.canary_write_code_parrainage as mod

    src = inspect.getsource(mod.main)
    # Two Save windows, each opened with phase_ref["name"] = "..." and
    # closed with phase_ref["name"] = None inside a finally block.
    assert src.count('phase_ref["name"] = "canary"') == 1
    assert src.count('phase_ref["name"] = "rollback"') == 1
    assert src.count('phase_ref["name"] = None') == 2
    idx_canary_open = src.index('phase_ref["name"] = "canary"')
    idx_canary_finally = src.index("finally:", idx_canary_open)
    idx_canary_reset = src.index('phase_ref["name"] = None', idx_canary_finally)
    idx_next_section = src.index("# --- CANARY: fresh reread", idx_canary_reset)
    assert idx_canary_open < idx_canary_finally < idx_canary_reset < idx_next_section
