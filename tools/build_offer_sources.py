#!/usr/bin/env python3
"""Bootstrap data/offer-sources.json for all offers.json programs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.monitor.models import SourceConfig
from lib.monitor.registry import save_registry
from lib.offers import OffersRepository

# Official-ish public pages (referral program marketing pages — no account login).
# Prefer brand referral program pages over personal invite links.
OFFICIAL = {
    "kraken": {
        "url": "https://www.kraken.com/en-us/features/referral-program",
        "type": "official_page",
        "parser": "generic_reward_html",
        "notes": "URL may redirect; 404 → REJECT not business change",
    },
    "coinbase": {
        "url": "https://www.coinbase.com/invite",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "binance": {
        "url": "https://www.binance.com/en/activity/referral",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "revolut": {
        "url": "https://www.revolut.com/referral/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "wise": {
        "url": "https://wise.com/invite",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "traderepublic": {
        "url": "https://traderepublic.com/fr-fr",
        "type": "official_page",
        "parser": "generic_reward_html",
        "notes": "referral amount often campaign-based / app-gated",
    },
    "robinhood": {
        "url": "https://robinhood.com/us/en/support/articles/referral-program/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "bybit": {
        "url": "https://www.bybit.com/en/help-center/article/Referral-Program",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "swissborg": {
        "url": "https://swissborg.com/fr/r",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "bitstack": {
        "url": "https://bitstack-app.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "deblock": {
        "url": "https://deblock.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "gemini": {
        "url": "https://www.gemini.com/share",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "ledger": {
        "url": "https://shop.ledger.com/pages/referral-program",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "paypal": {
        "url": "https://www.paypal.com/fr/webapps/mpp/referral",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "boursobank": {
        "url": "https://www.boursobank.com/offre-parrainage",
        "type": "official_page",
        "parser": "generic_reward_html",
        "notes": "campaign often suspended — expect REVIEW",
    },
    "betclic": {
        "url": "https://www.betclic.fr/parrainage",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "unibet": {
        "url": "https://www.unibet.fr/promotions",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "winamax": {
        "url": "https://www.winamax.fr/parrainage",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "parionssport": {
        "url": "https://www.enligne.parionssport.fdj.fr/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "heetch": {
        "url": "https://www.heetch.com/fr",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "omio": {
        "url": "https://www.omio.com/refer",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "airbnb": {
        "url": "https://www.airbnb.fr/help/article/2856",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "totalenergies": {
        "url": "https://www.totalenergies.fr/particuliers/parrainage",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "igraal": {
        "url": "https://fr.igraal.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "poulpeo": {
        "url": "https://www.poulpeo.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "ebuyclub": {
        "url": "https://www.ebuyclub.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "joko": {
        "url": "https://www.hellojoko.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "widilo": {
        "url": "https://www.widilo.fr/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "acheel": {
        "url": "https://www.acheel.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "lolivier": {
        "url": "https://www.olivierassurance.fr/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "finary": {
        "url": "https://finary.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "grass": {
        "url": "https://www.grass.io/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "fdj-francaise-des-jeux": {
        "url": "https://www.fdj.fr/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "nrj-mobile": {
        "url": "https://www.nrjmobile.fr/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "okx": {
        "url": "https://www.okx.com/fr/help/referral-program",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "plum": {
        "url": "https://withplum.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "vinted": {
        "url": "https://www.vinted.fr/help/96",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
    "whatnot": {
        "url": "https://www.whatnot.com/",
        "type": "official_page",
        "parser": "generic_reward_html",
    },
}


def main() -> int:
    offers = OffersRepository().load_all()
    configs = {}
    for o in offers:
        lk = o.get("lk")
        if not lk:
            continue
        meta = OFFICIAL.get(lk)
        if meta:
            configs[lk] = SourceConfig(
                program=lk,
                source_url=meta["url"],
                source_type=meta.get("type") or "official_page",
                extraction_method="http_fetch",
                fields_supported=["referee_reward", "conditions"],
                locale="fr",
                auth_required=False,
                parser=meta.get("parser") or "generic_reward_html",
                confidence_default="REVIEW",
                notes=meta.get("notes"),
                enabled=True,
            )
        else:
            configs[lk] = SourceConfig(
                program=lk,
                source_url=None,
                source_type="unmonitorable",
                extraction_method="none",
                fields_supported=[],
                locale="fr",
                auth_required=False,
                parser="static_canonical_hint",
                confidence_default="REJECT",
                notes="no_public_source_configured",
                enabled=False,
            )
    path = save_registry(configs)
    print(f"wrote {path} programs={len(configs)} with_url={sum(1 for c in configs.values() if c.source_url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
