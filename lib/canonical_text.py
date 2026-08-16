"""Shared strict canonical line-sequence comparison for compte<->public
post-write verification, used by every platform writer.

Originally built for parrainage-co (2026-08-16): its public renderer
inserts an extra blank line after every stored line break -- a purely
presentational difference that a literal substring/equality check has no
tolerance for, causing a genuinely correct write to be unconfirmable via
the public reread (even the pre-save baseline reread failed the same
way, proving it was a comparison bug, not a site problem). Factored out
of platforms/parrainage_co/writer.py so code-parrainage (same underlying
site family, same kind of rendering quirk observed in its own public
reread) and any future platform reuse the identical, already-tested
logic instead of a second, potentially-diverging copy.
"""
from __future__ import annotations


def canonical_lines(text: str) -> list[str]:
    """Canonicalize *text* to a sequence of non-empty, trimmed lines.

    Tolerates ONLY purely presentational differences: CRLF/LF, trailing
    whitespace per line, and how many blank lines separate two real lines
    (blank lines are dropped entirely, not collapsed-and-kept, so their
    count can never affect the comparison either way).

    Deliberately NOT permissive on anything else: non-empty text content,
    line order, amounts, code, link, punctuation, or any added/removed/
    reordered line all still produce a different sequence -- this is an
    exact sequence comparison, never a substring or fuzzy match.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def canonical_match(a: str, b: str) -> bool:
    """Exact canonical-line-sequence equality. Use for a public page where
    the extraction step already narrowly isolates just the offer block --
    nothing else should be present.
    """
    return canonical_lines(a) == canonical_lines(b)


def canonical_contains(haystack_text: str, needle_text: str) -> bool:
    """True iff needle's canonical line sequence appears as a contiguous
    run within haystack's canonical line sequence.

    Use for an account/edit reread that legitimately contains extra
    trailing lines beyond the main content field (e.g. other visible
    input values appended by a platform's own reread helper) -- exact
    equality would always fail there even on a perfect match.
    """
    haystack = canonical_lines(haystack_text)
    needle = canonical_lines(needle_text)
    if not needle:
        return False
    span = len(needle)
    for i in range(len(haystack) - span + 1):
        if haystack[i : i + span] == needle:
            return True
    return False
