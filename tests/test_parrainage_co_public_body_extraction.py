"""Regression tests for platforms/parrainage_co/writer.py::_extract_public_body.

Real incident (2026-08-16): the old implementation ran its offer-block
regex against RAW, un-stripped HTML first. A duplicate occurrence of the
offer's own phrasing ("Conditions de parrainage :") inside a meta tag's
attribute value, earlier in the document than the real offer block,
caused the non-greedy match to skip past the decoy and swallow real
markup fragments in between -- silently corrupting the extracted text
used for post-write verification. Even the pre-save baseline reread
failed under the old code, which is what exposed the bug (a freshly
fetched, untouched page cannot legitimately fail to match its own
current content).
"""
from __future__ import annotations

from platforms.parrainage_co.writer import _extract_public_body, _norm

KRAKEN_ORIGINAL = (
    "⭐️ Offre Parrainage Kraken – Jusqu’à 200 € offerts\n\n"
    "⚡ Bonus : 200 € en cryptomonnaies ⭐️\n"
    "Rejoignez Kraken, une plateforme d’échange crypto reconnue et sécurisée.\n\n"
    "⸻\n\n"
    "✅ Code parrain : cpbrgddy\n\n"
    "✅ Lien :\n\n"
    "https://invite.kraken.com/JDNW/s5qudqe4\n\n"
    "⸻\n\n"
    "✅ Étapes à suivre pour obtenir le bonus :\n\n"
    "Conditions de parrainage :\n\n"
    "Une fois que votre ami(e) aura terminé ces étapes, vous recevrez tous les deux le bonus.\n\n"
    "Créer et vérifier un compte\nDéposez au moins €200\nTradez au moins €200\n\n"
    "⸻\n\n"
    "ℹ️ À savoir :\n"
    "• Offre réservée aux nouveaux utilisateurs\n"
    "• Le bonus est crédité après validation des conditions sans action manuelle nécessaire\n\n"
    "⸻\n\n"
    "⭐️ Besoin d’aide ou d’un suivi personnalisé ?\n"
    "DM-moi — je te guide étape par étape\n\n"
    "↪️ https://discord.gg/dDEMb6jEbn ↩️"
)

KRAKEN_CANARY = KRAKEN_ORIGINAL.replace(
    "200 € en cryptomonnaies", "200 € en crypto-monnaies"
)


def _page_with_decoy_meta(offer_body_html: str) -> str:
    """Realistic page shape reproducing the real incident: a meta
    description earlier in <head> repeats "Conditions de parrainage :"
    (a decoy), followed later by the real offer block in the body,
    wrapped across several tags with <br> line breaks.
    """
    decoy = (
        "Code parrainage Kraken de Adrien89 — Conditions de parrainage : "
        "profitez du bonus en suivant les étapes."
    )
    body_html = offer_body_html.replace("\n", "<br>")
    return f"""<!doctype html>
<html>
<head>
<meta name="description" content="{decoy}">
<meta name="keywords" content="code parrainage Kraken de Adrien89, code parrain Kraken">
<script>var trackConditions = "Conditions de parrainage : fake js content";</script>
<style>.offer::before {{ content: "Conditions de parrainage :"; }}</style>
</head>
<body>
<nav>Parrainage.co<br>Ajouter mon annonce</nav>
<div class="offer-block">
<p>{body_html}</p>
</div>
<footer>© 2026 Parrainage.co</footer>
</body>
</html>"""


def test_extracts_real_original_text_past_decoy_meta_and_script_style():
    html = _page_with_decoy_meta(KRAKEN_ORIGINAL)
    extracted = _extract_public_body(html)
    assert _norm(KRAKEN_ORIGINAL) in _norm(extracted)
    # No leaked markup fragments from the decoy/script/style sections.
    assert "<meta" not in extracted
    assert "<script" not in extracted
    assert "trackConditions" not in extracted
    assert "content: " not in extracted
    assert '...">' not in extracted


def test_extracts_real_canary_text_past_decoy_meta():
    html = _page_with_decoy_meta(KRAKEN_CANARY)
    extracted = _extract_public_body(html)
    assert _norm(KRAKEN_CANARY) in _norm(extracted)
    assert "cryptomonnaies" not in extracted.replace("crypto-monnaies", "")


def test_original_and_canary_are_distinguishable_after_extraction():
    original = _extract_public_body(_page_with_decoy_meta(KRAKEN_ORIGINAL))
    canary = _extract_public_body(_page_with_decoy_meta(KRAKEN_CANARY))
    assert _norm(KRAKEN_ORIGINAL) in _norm(original)
    assert _norm(KRAKEN_ORIGINAL) not in _norm(canary)
    assert _norm(KRAKEN_CANARY) in _norm(canary)
    assert _norm(KRAKEN_CANARY) not in _norm(original)


def test_no_false_positive_when_real_offer_block_absent():
    """A page with ONLY the decoy meta/script/style text (no real offer
    block in the body) must never fabricate a match containing the full
    offer text."""
    html = """<!doctype html>
<html><head>
<meta name="description" content="Conditions de parrainage : voir les étapes.">
<script>var x = "Conditions de parrainage : also here";</script>
</head><body><nav>Parrainage.co</nav><p>Aucune offre trouvée.</p></body></html>"""
    extracted = _extract_public_body(html)
    assert "cryptomonnaies" not in extracted
    assert "Code parrain : cpbrgddy" not in extracted
    assert "Aucune offre trouvée" in extracted


def test_html_entities_are_unescaped():
    html = _page_with_decoy_meta(
        "⭐️ Offre Parrainage Kraken &ndash; Jusqu&rsquo;&agrave; 200&nbsp;&euro; offerts<br>"
        "Bonus&nbsp;: 200&nbsp;&euro; en cryptomonnaies &amp; plus&hellip;<br>"
        "https://discord.gg/dDEMb6jEbn ↩️"
    )
    extracted = _extract_public_body(html)
    assert "&ndash;" not in extracted
    assert "&nbsp;" not in extracted
    assert "&euro;" not in extracted
    assert "&amp;" not in extracted
    assert "&rsquo;" not in extracted
    assert "€" in extracted
    assert "cryptomonnaies" in extracted


def test_variable_whitespace_and_crlf_normalized():
    messy = KRAKEN_ORIGINAL.replace("\n\n", "\r\n\r\n\r\n   \r\n").replace("\n", "\r\n")
    html = _page_with_decoy_meta(messy)
    extracted = _extract_public_body(html)
    # No runs of 3+ blank lines, no trailing spaces before a newline, no bare \r.
    assert "\n\n\n" not in extracted
    assert "\r" not in extracted
    assert " \n" not in extracted
    assert _norm(KRAKEN_ORIGINAL) in _norm(extracted)


def test_matches_real_incident_shape_meta_description_before_body():
    """Reproduces the exact real-world shape that broke the old
    implementation: meta description contains the offer phrase, followed
    later by the real body wrapped in nested divs/spans with attributes
    containing quote characters that could prematurely close a naive
    raw-HTML regex span.
    """
    html = (
        '<!doctype html><html><head>'
        '<title>Code Parrainage kraken : cpbrgddy</title>'
        '<meta name="description" content="Code parrain Kraken cpbrgddy - '
        'Conditions de parrainage : suivez les étapes pour le bonus.">'
        '<link rel="canonical" href="https://parrainage.co/offers/113735">'
        '</head><body>'
        '<div class="wrapper" data-x="Conditions de parrainage :">'
        f'<p>{KRAKEN_ORIGINAL.replace(chr(10), "<br>")}</p>'
        '</div></body></html>'
    )
    extracted = _extract_public_body(html)
    assert _norm(KRAKEN_ORIGINAL) in _norm(extracted)
    assert "<meta" not in extracted
    assert "data-x" not in extracted
    assert '">' not in extracted
