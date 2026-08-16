"""tools/canary_write_parrainage_co.py — pre-browser logic + structural
guarantees. The live browser section itself is exercised for real only via
explicit operator-authorized GitHub Actions runs, not here.
"""
from __future__ import annotations

import inspect

import pytest

from lib.operator_overrides import OperatorOverrideStore
from tools.canary_write_parrainage_co import (
    CANARY_VALUE,
    FIELD,
    ORIGINAL_VALUE,
    PLATFORM,
    PROGRAM,
    _canary_and_original_renders,
)


def test_canary_and_original_renders_differ_only_in_reward():
    rendered_canary, rendered_original, var_canary, var_original = (
        _canary_and_original_renders()
    )
    assert var_canary["referee_reward"] == CANARY_VALUE
    assert var_original["referee_reward"] == ORIGINAL_VALUE
    assert var_canary["personal_code"] == var_original["personal_code"]
    assert var_canary["personal_link"] == var_original["personal_link"]
    assert rendered_canary != rendered_original
    assert CANARY_VALUE in rendered_canary
    assert ORIGINAL_VALUE in rendered_original
    assert ORIGINAL_VALUE not in rendered_canary
    assert CANARY_VALUE not in rendered_original


def test_override_store_is_clean_before_and_after():
    store = OperatorOverrideStore()

    def _platform_entries():
        return [
            o for o in store.load()
            if o.program == PROGRAM and o.field == FIELD and o.platform == PLATFORM
        ]

    assert _platform_entries() == []
    _canary_and_original_renders()
    assert _platform_entries() == [], "must never leave a stray PLATFORM_OPERATOR override behind"


def test_refuses_when_a_platform_override_already_exists():
    store = OperatorOverrideStore()
    store.upsert(PROGRAM, FIELD, "some other value", platform=PLATFORM, message="pre-existing")
    try:
        with pytest.raises(RuntimeError, match="already exists"):
            _canary_and_original_renders()
    finally:
        store.remove(PROGRAM, FIELD, platform=PLATFORM)


def test_rollback_is_unconditional_in_source_after_canary_click():
    """Structural guard matching the operator's MAY_HAVE_WRITTEN requirement:
    the rollback fill+save call must appear in source AFTER the canary
    save, and every reread/verification step in between must be unable to
    raise past it -- either because it's wrapped in its own try/except
    (the account reread), or because the helper itself never raises (the
    public reread) and every access to its result is None-guarded.
    """
    import tools.canary_write_parrainage_co as mod

    src = inspect.getsource(mod.main)
    idx_flag = src.index("canary_may_have_written = True")
    idx_canary_click = src.index("await _click_save(page)", idx_flag)
    idx_rollback_click = src.index("await _click_save(page)", idx_canary_click + 1)
    span = src[idx_canary_click:idx_rollback_click]

    assert "except Exception" in span, (
        "the account reread between canary-save and rollback-save must be "
        "individually caught so a failure there cannot skip the mandatory rollback"
    )
    # _reread_public() is the only other call in this span that touches the
    # network; it must never raise (it catches internally and returns
    # (None, error) instead) -- otherwise it WOULD be able to skip rollback.
    reread_public_src = inspect.getsource(mod._reread_public)
    assert "except Exception" in reread_public_src
    assert "raise" not in reread_public_src
    # Every dereference of the (possibly-None) account dump in this span
    # must be None-guarded.
    assert 'if canary_account_dump else None' in span
    assert '(canary_account_dump or {})' in span

    assert idx_canary_click < idx_rollback_click


def test_write_verified_promotion_gated_on_both_canary_and_rollback_ok():
    import tools.canary_write_parrainage_co as mod

    src = inspect.getsource(mod.main)
    idx_write_verified = src.index("write_verified = bool(canary_ok and rollback_ok")
    idx_promo = src.index("if write_verified:")
    assert idx_write_verified < idx_promo
