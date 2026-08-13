from __future__ import annotations

from typing import Callable

from lib.monitor.models import NormalizedOffer, SourceConfig
from lib.monitor.parsers.generic_reward_html import parse_generic_reward_html
from lib.monitor.parsers.program_specific import PROGRAM_PARSERS
from lib.monitor.parsers.static_hint import parse_static_from_canonical
from lib.monitor.parsers.structured_first import parse_structured_first

ParserFn = Callable[[str, SourceConfig, dict | None], NormalizedOffer]

PARSERS: dict[str, ParserFn] = {
    "generic_reward_html": parse_generic_reward_html,
    "structured_first": parse_structured_first,
    "static_canonical_hint": parse_static_from_canonical,
    **PROGRAM_PARSERS,
}


def get_parser(name: str) -> ParserFn:
    if name not in PARSERS:
        return parse_structured_first
    return PARSERS[name]
