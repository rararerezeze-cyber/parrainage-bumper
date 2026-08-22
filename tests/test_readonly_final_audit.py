from tools.readonly_final_audit import _remaining_work


def test_remaining_work_uses_current_proof_state():
    remaining = _remaining_work(
        {
            "1parrainage": {
                "status": "WRITE_VERIFIED",
                "gh_headless_save": "PROVEN",
            },
            "super-parrain": {
                "status": "WRITE_VERIFIED",
                "runtime_mode": "NORMAL_BUMP",
            },
            "code-parrainage": {
                "status": "WRITE_VERIFIED",
                "gh_headless_save": "PROVEN",
            },
            "referralcodes": {"autonomy": "IMPORT_UI_BETA_NOT_PROVEN"},
            "referralcode-tv": {
                "status": "WRITE_VERIFIED",
                "autonomy": "HUMAN_SAVE_REQUIRED",
                "save_requires_captcha": True,
            },
            "referraldrop": {"status": "AUTH_BLOCKED_MANUAL"},
        }
    )

    joined = "\n".join(remaining)
    assert "1parrainage:" not in joined
    assert "super-parrain:" not in joined
    assert "code-parrainage:" not in joined
    assert "NEVER_AUTO_COMMIT" in joined
    assert "HUMAN_SAVE_REQUIRED" in joined
    assert "AUTH_BLOCKED_MANUAL" in joined


def test_remaining_work_preserves_unproven_states():
    remaining = _remaining_work(
        {
            "1parrainage": {
                "status": "WRITE_VERIFIED",
                "gh_headless_save": "NOT_RUN",
            },
            "super-parrain": {
                "status": "CANARY_READY",
                "runtime_mode": "CANARY_PENDING",
            },
            "code-parrainage": {
                "status": "WRITE_VERIFIED",
                "gh_headless_save": "NOT_RUN",
            },
        }
    )

    joined = "\n".join(remaining)
    assert "1parrainage: unattended GH save proof incomplete" in joined
    assert "super-parrain: content write proof incomplete" in joined
    assert "code-parrainage: unattended save proof incomplete" in joined
