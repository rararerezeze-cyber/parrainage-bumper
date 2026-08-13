from lib.mapping_guards import write_blocked_reason


def test_paypal_stale_blocked():
    reason = write_blocked_reason("parrainage-co", "paypal")
    assert reason is not None
    assert "NOT_PRESENT" in reason or "stale" in reason.lower() or "status=" in reason


def test_kraken_not_blocked():
    reason = write_blocked_reason("parrainage-co", "kraken")
    assert reason is None
