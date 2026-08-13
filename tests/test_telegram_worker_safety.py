"""Unit tests for Cloudflare worker command safety (mirror of worker.js logic)."""
from __future__ import annotations

import re


def is_safe_command(text: str) -> bool:
    """Python mirror of telegram-worker/worker.js isSafeCommand."""
    if not text or len(text) > 4000:
        return False
    if re.search(r"[;&|`$<>\\]", text):
        return False
    if re.search(r"\b(curl|wget|bash|powershell|cmd\.exe)\b", text, re.I):
        return False
    t = text.lower()
    if re.search(r"\b(status|overrides)\b", t):
        return bool(re.search(r"[a-z]{2,}", t))
    if re.search(r"\bsupprimer\s+override\b", t):
        return True
    field = re.search(
        r"\b(code|lien|link|gain|filleul|parrain|reward|conditions?|cond|depot|délai|delai|jours|expiry|title|titre|minimum|spend|trade|transaction)\b",
        text,
        re.I,
    )
    has_word = bool(re.search(r"[A-Za-z]{2,}", text))
    return bool(field and has_word)


def test_allows_operator_commands():
    assert is_safe_command("Kraken gain filleul 20 €")
    assert is_safe_command("Kraken Super-Parrain gain filleul 25 €")
    assert is_safe_command("Kraken code ABC123")
    assert is_safe_command("Kraken lien https://example.com/x")
    assert is_safe_command("Kraken status")
    assert is_safe_command("Kraken supprimer override gain filleul")
    assert is_safe_command("Kraken conditions Déposer 100 €")


def test_rejects_injection_and_shell():
    assert not is_safe_command("Kraken code ABC; rm -rf /")
    assert not is_safe_command("curl http://evil")
    assert not is_safe_command("Kraken `whoami`")
    assert not is_safe_command("")


def test_rejects_unknown_noise():
    assert not is_safe_command("hello world random")
    assert not is_safe_command("????")
