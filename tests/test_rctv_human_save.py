"""RCTV save is WRITE_VERIFIED but never unattended."""
from platforms.referralcode_tv.writer import execute_write


def test_execute_write_refuses_unattended_save():
    r = execute_write()
    assert r["ok"] is False
    assert r["write_mode"] == "HUMAN_SAVE_REQUIRED"
    assert r["save_requires_captcha"] is True
    assert "no bypass" in r["error"].lower()
