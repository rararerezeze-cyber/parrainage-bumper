"""platforms/parrainage_co/writer.py — canonical compte<->public comparison.

Platform-specific, strict normalization: tolerates ONLY CRLF/LF, trailing
per-line whitespace, and blank-line multiplicity (parrainage.co's public
renderer inserts an extra blank line after every stored line break — see
_canonical_lines' docstring for the real incident this fixes). Must never
become permissive on actual content: text, line order, amounts, code,
link, punctuation, or any added/removed/reordered line.
"""
from __future__ import annotations

from platforms.parrainage_co.writer import (
    _canonical_contains,
    _canonical_lines,
    _canonical_match,
)

ORIGINAL = (
    "⭐️ Offre Parrainage Kraken – Jusqu’à 200 € offerts\n"
    "⚡ Bonus : 200 € en cryptomonnaies ⭐️\n"
    "Rejoignez Kraken, une plateforme d’échange crypto reconnue et sécurisée.\n"
    "✅ Code parrain : cpbrgddy\n"
    "✅ Lien : https://invite.kraken.com/JDNW/s5qudqe4"
)


# 1. Same content + extra blank lines introduced by public HTML rendering => MATCH
def test_extra_blank_lines_from_html_rendering_still_match():
    with_extra_blanks = "\n\n".join(ORIGINAL.split("\n"))  # every line break becomes a blank line
    assert _canonical_match(with_extra_blanks, ORIGINAL) is True


def test_crlf_and_trailing_whitespace_still_match():
    messy = ORIGINAL.replace("\n", "  \r\n\r\n   \r\n")
    assert _canonical_match(messy, ORIGINAL) is True


# 2. crypto-monnaies vs cryptomonnaies => NO MATCH
def test_canary_reward_text_does_not_match_original():
    canary = ORIGINAL.replace("cryptomonnaies", "crypto-monnaies")
    assert _canonical_match(canary, ORIGINAL) is False


# 3. 200 € vs 100 € => NO MATCH
def test_different_amount_does_not_match():
    other_amount = ORIGINAL.replace("200 €", "100 €")
    assert _canonical_match(other_amount, ORIGINAL) is False


# 4. code modifié => NO MATCH
def test_modified_code_does_not_match():
    other_code = ORIGINAL.replace("cpbrgddy", "WRONGCODE")
    assert _canonical_match(other_code, ORIGINAL) is False


# 5. lien modifié => NO MATCH
def test_modified_link_does_not_match():
    other_link = ORIGINAL.replace(
        "https://invite.kraken.com/JDNW/s5qudqe4", "https://invite.kraken.com/XXXX/wrong"
    )
    assert _canonical_match(other_link, ORIGINAL) is False


# 6. ligne de texte manquante => NO MATCH
def test_missing_line_does_not_match():
    lines = ORIGINAL.split("\n")
    missing_one = "\n".join(lines[:-1])  # drop the last line (the link)
    assert _canonical_match(missing_one, ORIGINAL) is False


# 7. ordre de deux lignes modifié => NO MATCH
def test_reordered_lines_do_not_match():
    lines = ORIGINAL.split("\n")
    lines[0], lines[1] = lines[1], lines[0]
    reordered = "\n".join(lines)
    assert _canonical_match(reordered, ORIGINAL) is False


# --- additional coverage -----------------------------------------------

def test_added_punctuation_does_not_match():
    with_extra_punct = ORIGINAL.replace("200 € offerts", "200 € offerts!!!")
    assert _canonical_match(with_extra_punct, ORIGINAL) is False


def test_added_stray_character_does_not_match():
    with_typo = ORIGINAL.replace("Rejoignez", "RRejoignez")
    assert _canonical_match(with_typo, ORIGINAL) is False


def test_empty_text_never_matches_real_content():
    assert _canonical_match("", ORIGINAL) is False
    assert _canonical_match("\n\n\n   \n", ORIGINAL) is False


def test_canonical_lines_drops_empty_but_keeps_order_and_content():
    text = "  a  \n\n\n b \n\nc\n"
    assert _canonical_lines(text) == ["a", "b", "c"]


# --- _canonical_contains (account reread, which has extra trailing lines) --

def test_contains_matches_when_needle_is_prefix_of_haystack():
    haystack = ORIGINAL + "\ncpbrgddy\nhttps://invite.kraken.com/JDNW/s5qudqe4"
    assert _canonical_contains(haystack, ORIGINAL) is True


def test_contains_false_when_needle_absent():
    haystack = "cpbrgddy\nhttps://invite.kraken.com/JDNW/s5qudqe4"
    assert _canonical_contains(haystack, ORIGINAL) is False


def test_contains_false_when_needle_only_partially_present_out_of_order():
    lines = ORIGINAL.split("\n")
    scrambled_haystack = "\n".join(reversed(lines)) + "\nextra line"
    assert _canonical_contains(scrambled_haystack, ORIGINAL) is False


def test_contains_rejects_canary_value_even_if_rest_matches():
    haystack = ORIGINAL.replace("cryptomonnaies", "crypto-monnaies") + "\nextra"
    assert _canonical_contains(haystack, ORIGINAL) is False
