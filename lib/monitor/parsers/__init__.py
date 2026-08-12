from __future__ import annotations

from typing import Callable

from lib.monitor.models import NormalizedOffer, SourceConfig
from lib.monitor.parsers.generic_reward_html import parse_generic_reward_html
from lib.monitor.parsers.static_hint import parse_static_from_canonical

ParserFn = Callable[[str, SourceConfig, dict | None], NormalizedOffer]

PARSERS: dict[str, ParserFn] = {
    "generic_reward_html": parse_generic_reward_html,
    "static_canonical_hint": parse_static_from_canonical,
}


def get_parser(name: str) -> ParserFn:
    if name not in PARSERS:
        return parse_generic_reward_html
    return PARSERS[name]
