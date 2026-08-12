#!/usr/bin/env python3
"""Build data/offer-sources.json with source classification + impact priority.

source_class is NOT "URL present" — it is an explicit verification category.
Observation-only: never writes offers.json or platform ads.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.monitor.models import (
    DEFAULT_FIELD_SOURCES,
    OfferKind,
    SourceClass,
    SourceConfig,
)
from lib.monitor.registry import mapping_impact_counts, save_registry
from lib.offers import OffersRepository

# Field authority defaults (monitor never owns personal codes/links)
FS_PUBLIC = dict(DEFAULT_FIELD_SOURCES)
FS_OPERATOR_REWARD = {
    **DEFAULT_FIELD_SOURCES,
    "referee_reward": "OPERATOR",
    "referrer_reward": "OPERATOR",
    "conditions": "OPERATOR",
}
FS_APP = {
    **DEFAULT_FIELD_SOURCES,
    "referee_reward": "OPERATOR",  # app-personalized amounts not public SoT
    "referrer_reward": "OPERATOR",
    "conditions": "OFFICIAL_PUBLIC_MONITOR",  # general rules may be public
}

# program → source definition
# Prefer: exact referral page > help center > terms > public API > general only if data present
OFFICIAL: dict[str, dict] = {
    # --- High impact first (impact computed separately) ---
    "bybit": {
        "url": "https://www.bybit.com/en/help-center/article/Referral-Program",
        "parser": "bybit_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "reward_type", "conditions"],
        "notes": "Help center referral program; commission rates may vary by campaign",
    },
    "joko": {
        "url": "https://www.hellojoko.com/",
        "parser": "cashback_portal_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Homepage; welcome bonus often in app/landing variants",
    },
    "unibet": {
        "url": "https://www.unibet.fr/promotions",
        "parser": "unibet_promos_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Promotions hub — multi-offers; parrainage may be nested",
    },
    "widilo": {
        "url": "https://www.widilo.fr/",
        "parser": "cashback_portal_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward"],
    },
    "airbnb": {
        "url": "https://www.airbnb.fr/help/article/2856",
        "parser": "airbnb_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "reward_type", "conditions"],
        "notes": "Official help article on referral credits",
    },
    "coinbase": {
        "url": "https://www.coinbase.com/invite",
        "parser": "coinbase_referral_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "trade_min", "conditions"],
        "notes": "Invite page; amounts often campaign/geo dependent",
    },
    "ebuyclub": {
        "url": "https://www.ebuyclub.com/",
        "parser": "cashback_portal_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward"],
    },
    "gemini": {
        "url": "https://www.gemini.com/share",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "igraal": {
        "url": "https://fr.igraal.com/parrainage",
        "parser": "igraal_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_STABLE.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Prefer /parrainage over homepage multi-cashback noise",
    },
    "kraken": {
        "url": "https://www.kraken.com/referrals",
        "parser": "kraken_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "trade_min", "qualification_days", "reward_type", "conditions"],
        "notes": "Official /referrals page (verified 200). Terms: /legal/referrals",
    },
    "ledger": {
        "url": "https://shop.ledger.com/pages/referral-program",
        "parser": "ledger_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "reward_type", "conditions"],
    },
    "revolut": {
        "url": "https://www.revolut.com/referral/",
        "parser": "app_personalized_stub",
        "source_class": SourceClass.AUTH_APP_ONLY.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["conditions"],
        "notes": "Reward amounts are in-app personalized — never public HIGH as SoT",
    },
    "swissborg": {
        "url": "https://swissborg.com/fr",
        "parser": "swissborg_referral_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "min_deposit", "conditions"],
        "notes": "Academy URLs 404; FR homepage is reachable (referral details often app)",
    },
    "betclic": {
        "url": "https://www.betclic.fr/aide/parrainage",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Help center path; may 404 → reclassify",
    },
    "bitstack": {
        "url": "https://bitstack-app.com/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["referee_reward", "conditions"],
        "notes": "App-first product; public homepage may lack fixed reward",
    },
    "boursobank": {
        "url": "https://www.boursobank.com/bon-plan/parrainage-boursobank",
        "parser": "boursobank_parrainage_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "min_deposit", "qualification_days", "reward_type", "campaign_variant", "conditions"],
        "notes": "Public bon-plan; multi-tier primes — parser models max + deposit, not insurance prices",
    },
    "heetch": {
        "url": "https://www.heetch.com/fr",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "/fr/driver 404; FR homepage is reachable",
    },
    "okx": {
        "url": "https://www.okx.com/join",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Help articles 404; /join is public entry (rates may be account-gated)",
    },
    "paypal": {
        "url": "https://www.paypal.com/uk/webapps/mpp/invite/terms",
        "parser": "paypal_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "min_spend", "qualification_days", "conditions"],
        "notes": "Official invite terms (UK). FR popup path 404. Geo-campaign dependent.",
    },
    "poulpeo": {
        "url": "https://www.poulpeo.com/",
        "parser": "cashback_portal_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward"],
    },
    "robinhood": {
        "url": "https://robinhood.com/us/en/support/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Specific referral article 404; support hub is live. US-centric.",
    },
    "totalenergies": {
        "url": "https://www.totalenergies.fr/",
        "parser": "totalenergies_referral_fr",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "/particuliers/parrainage 404; homepage only for public fetch",
    },
    "traderepublic": {
        "url": "https://traderepublic.com/fr-fr/support",
        "parser": "app_personalized_stub",
        "source_class": SourceClass.AUTH_APP_ONLY.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["conditions"],
        "notes": "Referral amount is app/campaign gated — not a public fixed offer",
    },
    "vinted": {
        "url": "https://www.vinted.fr/help/96",
        "parser": "vinted_referral_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "reward_type", "conditions"],
    },
    "whatnot": {
        "url": "https://www.whatnot.com/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Help article 404; homepage reachable (referral may be app-only)",
    },
    "winamax": {
        "url": "https://www.winamax.fr/parrainage",
        "parser": "winamax_parrainage_fr",
        "source_class": SourceClass.VERIFIED_OFFICIAL.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "reward_type", "conditions"],
    },
    "acheel": {
        "url": "https://www.acheel.com/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "binance": {
        "url": "https://www.binance.com/en/activity/referral",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "Often anti-bot / JS-heavy",
    },
    "deblock": {
        "url": "https://deblock.com/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["referee_reward", "conditions"],
    },
    "nrj-mobile": {
        "url": "https://www.nrjmobile.fr/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "omio": {
        "url": "https://www.omio.com/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
        "notes": "/refer and help referral 410; homepage only",
    },
    "plum": {
        "url": "https://withplum.com/",
        "parser": "app_personalized_stub",
        "source_class": SourceClass.AUTH_APP_ONLY.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["conditions"],
    },
    "wise": {
        "url": "https://wise.com/invite",
        "parser": "app_personalized_stub",
        "source_class": SourceClass.AUTH_APP_ONLY.value,
        "offer_kind": OfferKind.APP_PERSONALIZED.value,
        "field_sources": FS_APP,
        "fields": ["conditions"],
        "notes": "Invite often personal link; fee discount personalized",
    },
    "fdj-francaise-des-jeux": {
        "url": "https://www.fdj.fr/",
        "parser": "structured_first",
        "source_class": SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value,
        "offer_kind": OfferKind.OPERATOR_ONLY.value,
        "field_sources": FS_OPERATOR_REWARD,
        "fields": [],
        "notes": "No stable public referral program page found",
    },
    "finary": {
        "url": "https://finary.com/fr",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "lolivier": {
        "url": "https://www.olivierassurance.fr/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "parionssport": {
        "url": "https://www.enligne.parionssport.fdj.fr/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
    "grass": {
        "url": "https://www.grass.io/",
        "parser": "structured_first",
        "source_class": SourceClass.OFFICIAL_BUT_NOT_MACHINE_READABLE.value,
        "offer_kind": OfferKind.PUBLIC_CAMPAIGN.value,
        "fields": ["referee_reward", "conditions"],
    },
}


def main() -> int:
    offers = OffersRepository().load_all()
    impact = mapping_impact_counts()
    configs: dict[str, SourceConfig] = {}
    for o in offers:
        lk = o.get("lk")
        if not lk:
            continue
        meta = OFFICIAL.get(lk)
        if meta:
            configs[lk] = SourceConfig(
                program=lk,
                source_url=meta.get("url"),
                source_type=meta.get("type") or "official_page",
                extraction_method="http_fetch",
                fields_supported=list(meta.get("fields") or ["referee_reward", "conditions"]),
                locale="fr",
                auth_required=meta.get("auth_required", False),
                parser=meta.get("parser") or "structured_first",
                confidence_default="REVIEW",
                notes=meta.get("notes"),
                enabled=True,
                source_class=meta.get("source_class") or SourceClass.UNVERIFIED.value,
                offer_kind=meta.get("offer_kind") or OfferKind.PUBLIC_CAMPAIGN.value,
                field_sources=meta.get("field_sources") or FS_PUBLIC,
                structured_endpoint=meta.get("structured_endpoint"),
                impact_count=impact.get(lk, 0),
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
                parser="operator_only_stub",
                confidence_default="REJECT",
                notes="no_public_source_configured",
                enabled=False,
                source_class=SourceClass.NO_PUBLIC_REFERRAL_SOURCE.value,
                offer_kind=OfferKind.OPERATOR_ONLY.value,
                field_sources=FS_OPERATOR_REWARD,
                impact_count=impact.get(lk, 0),
            )
    path = save_registry(configs)
    print(
        f"wrote {path} programs={len(configs)} "
        f"verified={sum(1 for c in configs.values() if c.source_class == SourceClass.VERIFIED_OFFICIAL.value)} "
        f"app={sum(1 for c in configs.values() if c.offer_kind == OfferKind.APP_PERSONALIZED.value)} "
        f"mappings={sum(impact.values())}"
    )
    # priority table
    for c in sorted(configs.values(), key=lambda x: (-x.impact_count, x.program))[:15]:
        print(f"  impact={c.impact_count:2d} {c.program:20} {c.source_class:35} {c.parser}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
