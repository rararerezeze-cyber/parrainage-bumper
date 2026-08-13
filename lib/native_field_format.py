"""Adapt a monitor/canonical value to a platform-native mutable span.

Never invents a phrase. If the incoming value is a simple amount/days token,
the published span keeps its wording and only the number is updated.
Structured incoming phrases (Winamax bonus+freebets) pass through unchanged.
Non-amount incoming values never replace a native amount span.
"""
from __future__ import annotations

import re

AMOUNT_FIELDS = frozenset({"referee_reward", "min_deposit", "referrer_reward"})

# Optional "jusqu'à" + number + euro/dollar. Whole-string = simple monitor token.
_SIMPLE_AMOUNT = re.compile(
    r"(?is)^\s*(?:jusqu['’` ]?\s*à\s+)?(\d+(?:[.,]\d+)?)\s*(€|eur|euros?|\$)\s*$"
)
_ANY_AMOUNT = re.compile(
    r"(?i)(\d+(?:[.,]\d+)?)\s*(€|eur|euros?|\$)"
)
_SIMPLE_DAYS = re.compile(r"^\s*(\d+)\s*(?:j|jours?)?\s*$", re.I)


def _replace_first_number(text: str, number: str) -> str:
    return re.sub(r"\d+(?:[.,]\d+)?", number, text, count=1)


def adapt_monitor_value_to_native(
    field: str,
    incoming: str | None,
    native: str | None,
) -> str | None:
    """Return the value that should be injected into a native template span."""
    if incoming is None:
        return native
    incoming = str(incoming)
    if native is None or str(native) == "":
        return incoming
    native = str(native)
    field = (field or "").strip()

    if field == "qualification_days":
        m = _SIMPLE_DAYS.match(incoming.strip())
        if m and re.search(r"\d+", native):
            return _replace_first_number(native, m.group(1))
        return incoming

    if field not in AMOUNT_FIELDS:
        return incoming

    inc_simple = _SIMPLE_AMOUNT.match(incoming.strip())
    inc_any = _ANY_AMOUNT.search(incoming)
    nat_any = _ANY_AMOUNT.search(native)

    if inc_any is None and nat_any is not None:
        # e.g. offers.json "Programme à confirmer" must not overwrite "200 €"
        return native

    if inc_simple and nat_any:
        return _replace_first_number(native, inc_simple.group(1))

    return incoming
