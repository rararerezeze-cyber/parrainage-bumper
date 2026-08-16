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
    _account_phase_evidence,
    _canary_and_original_renders,
    _public_phase_evidence,
    _sha256,
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


def test_override_store_file_is_byte_identical_after_render_computation():
    """Regression for a real incident (2026-08-16, run 31950145270):
    OperatorOverrideStore.save() always bumps `updated_at`, so a plain
    add-then-remove left data/operator-overrides.json with a changed
    timestamp (content identical) -- an uncommitted, unstaged diff on a
    tracked file that made `git pull --rebase` refuse in the commit step,
    silently losing the run's WRITE_VERIFIED promotion even though the
    canary+rollback had already succeeded for real.
    """
    from lib.paths import OPERATOR_OVERRIDES_PATH

    before = OPERATOR_OVERRIDES_PATH.read_bytes()
    _canary_and_original_renders()
    after = OPERATOR_OVERRIDES_PATH.read_bytes()
    assert after == before, "operator-overrides.json must be byte-identical, not just content-equal"


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
    # _fetch_public_evidence() is the only other call in this span that
    # touches the network; it must never raise (it catches internally and
    # returns an evidence dict with an "error" key instead) -- otherwise it
    # WOULD be able to skip rollback.
    reread_public_src = inspect.getsource(mod._fetch_public_evidence)
    assert "except Exception" in reread_public_src
    assert "raise" not in reread_public_src

    assert idx_canary_click < idx_rollback_click


def test_sha256_is_deterministic_and_distinguishes_content():
    assert _sha256("abc") == _sha256("abc")
    assert _sha256("abc") != _sha256("abd")
    assert _sha256(None) is None


def test_account_phase_evidence_extracts_code_link_and_match():
    dump = {
        "inputs": [
            {"name": "ref_code", "preview": "cpbrgddy"},
            {"name": "ref_link", "preview": "https://invite.kraken.com/JDNW/s5qudqe4"},
            {"name": "content", "preview": "⭐️ preview..."},
        ]
    }
    plain_text = ORIGINAL_VALUE + "\ncpbrgddy\nhttps://invite.kraken.com/JDNW/s5qudqe4"
    ev = _account_phase_evidence("before", dump, plain_text, plain_text)
    assert ev["ref_code"] == "cpbrgddy"
    assert ev["ref_link"] == "https://invite.kraken.com/JDNW/s5qudqe4"
    assert ev["contains_original_reward"] is True
    assert ev["contains_canary_reward"] is False
    assert ev["canonical_match_vs_expected"] is True
    assert ev["raw_text_sha256"] == _sha256(plain_text)


def test_account_phase_evidence_flags_canary_value_present():
    plain_text = CANARY_VALUE + "\ncpbrgddy\nhttps://invite.kraken.com/JDNW/s5qudqe4"
    ev = _account_phase_evidence("canary", {"inputs": []}, plain_text, plain_text)
    assert ev["contains_canary_reward"] is True
    assert ev["contains_original_reward"] is False


def test_public_phase_evidence_hashes_raw_html_separately_from_extracted():
    extracted = ORIGINAL_VALUE
    ev = _public_phase_evidence("before", "deadbeef", extracted, extracted)
    assert ev["raw_html_sha256"] == "deadbeef"
    assert ev["extracted_text_sha256"] == _sha256(extracted)
    assert ev["canonical_match_vs_expected"] is True


def test_public_phase_evidence_no_match_on_canary_vs_original_expected():
    ev = _public_phase_evidence("canary", "x", CANARY_VALUE, ORIGINAL_VALUE)
    assert ev["canonical_match_vs_expected"] is False
    assert ev["contains_canary_reward"] is True


def test_write_verified_promotion_gated_on_both_canary_and_rollback_ok():
    import tools.canary_write_parrainage_co as mod

    src = inspect.getsource(mod.main)
    idx_write_verified = src.index("write_verified = bool(")
    idx_promo = src.index("if write_verified:")
    assert idx_write_verified < idx_promo
    verdict_span = src[idx_write_verified:idx_promo]
    assert "canary_ok" in verdict_span
    assert "rollback_ok" in verdict_span


def test_promotion_evidence_uses_the_keys_mark_write_verified_actually_requires():
    """Regression for a real incident (2026-08-16, run 31950145270): the
    canary script's own write_verified=True was correct (every real
    criterion was proven), but mark_write_verified() rejected the
    promotion with 'incomplete_evidence' because the evidence dict passed
    `report` (canary_ok/rollback_ok/phases/...) directly as `checks`,
    instead of lib.write_status.REQUIRED_VERIFY_CHECKS' actual key names.
    The mapping/golden files got updated locally on the runner regardless
    (that write isn't gated on mark_write_verified's return), so
    data/platform-write-status.json silently fell out of sync with the
    mapping file's own write_status field until this was caught and fixed
    by hand from the run's raw logs.
    """
    from lib.write_status import REQUIRED_VERIFY_CHECKS

    import tools.canary_write_parrainage_co as mod

    src = inspect.getsource(mod.main)
    idx = src.index("legacy_checks = {")
    idx_call = src.index("mark_write_verified(", idx)
    checks_block = src[idx:idx_call]
    for key in REQUIRED_VERIFY_CHECKS:
        assert f'"{key}"' in checks_block, f"legacy_checks is missing required key {key!r}"
    # And the call must actually pass `checks=legacy_checks`, not the raw report.
    assert "evidence = {" in src[idx_call - 400 : idx_call]
    assert 'checks": legacy_checks' in src[idx_call - 400 : idx_call]
