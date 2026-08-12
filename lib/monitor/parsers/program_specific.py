"""Program-specific extraction strategies (deterministic, public pages only)."""
from __future__ import annotations

import hashlib
import re
from html import unescape

from lib.monitor.models import Confidence, FailureCode, NormalizedOffer, SourceConfig
from lib.monitor.normalize import is_plausible_reward, normalize_reward
from lib.monitor.parsers.structured_first import parse_structured_first


def _text(html: str) -> str:
    t = re.sub(r"(?is)<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = unescape(t)
    return " ".join(t.split())


def _fp(html: str) -> str:
    return hashlib.sha256((html or "")[:50000].encode("utf-8", errors="replace")).hexdigest()[:16]


def _base(
    cfg: SourceConfig,
    html: str,
    fields: dict,
    conf: Confidence,
    notes: list[str],
    code: FailureCode = FailureCode.NONE,
    mode: str = "html",
) -> NormalizedOffer:
    return NormalizedOffer(
        program=cfg.program,
        fields={k: v for k, v in fields.items() if v},
        confidence=conf,
        parser=cfg.parser,
        source_url=cfg.source_url,
        raw_fingerprint=_fp(html),
        notes=notes,
        failure_code=code,
        extraction_mode=mode,
    )


def parse_igraal_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    st = parse_structured_first(html, cfg, offer)
    if st.extraction_mode == "structured_json" and st.confidence == Confidence.HIGH:
        return st
    text = _text(html)
    m = re.search(
        r"(?i)(?:parrainage|invitation|filleul|bonus de bienvenue)[^\d]{0,40}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?\s+à\s+l['’]inscription|à l['’]inscription)", text)
    if m:
        reward = normalize_reward(m.group(1))
        if reward and is_plausible_reward(reward):
            return _base(cfg, html, {"referee_reward": reward}, Confidence.HIGH, ["igraal_invitation_amount"])
    return _base(
        cfg,
        html,
        {"referee_reward": st.fields.get("referee_reward")},
        Confidence.REVIEW,
        ["igraal_ambiguous_many_amounts"] + st.notes,
        FailureCode.AMBIGUOUS_REWARD,
    )


def parse_cashback_portal_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    m = re.search(
        r"(?i)(?:inscription|bienvenue|parrainage|offre de bienvenue)[^\d]{0,50}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?\s+à\s+l['’]inscription|de\s+bienvenue)", text)
    if m:
        reward = normalize_reward(m.group(1))
        if reward and is_plausible_reward(reward):
            nums = re.findall(r"\d+", reward.replace(" ", ""))
            if nums and int(nums[0]) > 50:
                return _base(cfg, html, {}, Confidence.REVIEW, ["welcome_amount_too_large"], FailureCode.AMBIGUOUS_REWARD)
            return _base(cfg, html, {"referee_reward": reward}, Confidence.HIGH, ["cashback_welcome_bonus"])
    return _base(cfg, html, {}, Confidence.REVIEW, ["cashback_no_clear_welcome"], FailureCode.AMBIGUOUS_REWARD)


def parse_ledger_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    m2 = re.search(r"(?i)(?:referral|parrainage)[^\d]{0,60}(\d[\d\s.,]*\s*€)", text)
    m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:en\s+)?(?:bitcoin|btc|crypto)?", text)
    pick = m2.group(1) if m2 else (m.group(1) if m else None)
    if pick:
        reward = normalize_reward(pick)
        if reward and is_plausible_reward(reward):
            fields = {"referee_reward": reward, "reward_type": "crypto"}
            return _base(cfg, html, fields, Confidence.HIGH, ["ledger_referral_amount"])
    return parse_structured_first(html, cfg, offer)


def parse_winamax_parrainage_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    fields: dict[str, str | None] = {}
    m = re.search(
        r"(?i)(\d+)\s*€\s*(?:cash|espèces|bonus)?\s*(?:\+|et)\s*(\d+)\s*€\s*(?:freebet|freebets?|free bets?)",
        text,
    )
    if not m:
        m = re.search(
            r"(?i)(\d+)\s*€\s*bonus\s*\+\s*(\d+)\s*€\s*(?:en\s+)?freebets?",
            text,
        )
    if m:
        fields["referee_reward"] = normalize_reward(f"{m.group(1)} € bonus + {m.group(2)} € freebets")
        fields["reward_type"] = "cash+freebet"
        dep = re.search(r"(?i)d[ée]p[oô]t\s*(?:d['’]un\s*)?minimum\s*(?::\s*)?(\d+)\s*€", text)
        if dep:
            fields["min_deposit"] = normalize_reward(f"{dep.group(1)} €")
        return _base(cfg, html, fields, Confidence.HIGH, ["winamax_cash_freebet_pair"])
    m2 = re.search(r"(?i)jusqu['’]à\s*(\d+)\s*€", text)
    if m2:
        fields["referee_reward"] = normalize_reward(f"jusqu'à {m2.group(1)} €")
        return _base(cfg, html, fields, Confidence.REVIEW, ["winamax_jusqua_only"], FailureCode.AMBIGUOUS_REWARD)
    return parse_structured_first(html, cfg, offer)


def parse_kraken_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """Kraken: prefer 'vous et votre ami recevez X €' over generic multi-amounts."""
    st = parse_structured_first(html, cfg, offer)
    if st.extraction_mode == "structured_json" and st.confidence == Confidence.HIGH:
        st.parser = cfg.parser
        return st
    text = _text(html)
    fields: dict[str, str | None] = {}
    # Strong pattern from official /referrals FR page
    m_pair = re.search(
        r"(?i)(?:vous et votre ami|you and your friend)\s+recevez\s+(\d[\d\s.,]*)\s*€",
        text,
    )
    m2 = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:en\s+)?(?:bitcoin|btc)", text)
    m = re.search(
        r"(?i)(?:up to|jusqu['’]à|earn|recevez|get)\s*(?:up to\s*)?(\d[\d\s.,]*)\s*€",
        text,
    )
    pick = None
    if m_pair:
        pick = f"{m_pair.group(1)} €"
    elif m2:
        pick = m2.group(1)
    elif m:
        pick = f"{m.group(1)} €"
    if pick:
        has_btc = "bitcoin" in text.lower() or "btc" in text.lower()
        reward = normalize_reward(f"{pick} en Bitcoin" if has_btc else pick)
        if reward and is_plausible_reward(reward):
            fields["referee_reward"] = reward
            fields["reward_type"] = "crypto" if has_btc else "cash"
    trade = re.search(r"(?i)(?:trade|échanger|acheter)[^\d]{0,40}(\d[\d\s.,]*\s*€)", text)
    if trade:
        fields["trade_min"] = normalize_reward(trade.group(1))
    days = re.search(r"(?i)(?:within|dans les|sous)\s*(\d+)\s*(?:days?|jours?)", text)
    if days:
        fields["qualification_days"] = days.group(1)
    if fields.get("referee_reward") and (m_pair or fields.get("trade_min")):
        return _base(cfg, html, fields, Confidence.HIGH, ["kraken_pair_or_trade_context"])
    if fields.get("referee_reward"):
        return _base(cfg, html, fields, Confidence.HIGH, ["kraken_reward_amount"])
    st.notes = ["kraken_fallback"] + st.notes
    st.parser = cfg.parser
    return st


def parse_swissborg_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    fields: dict[str, str | None] = {}
    # Require explicit referral/parrainage context — do NOT pick converter amounts (EUR €100)
    m = re.search(
        r"(?i)(?:parrainage|referral|filleul|ami parrainé|invitez un ami)[^\d]{0,60}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(
            r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?|de\s+bonus)?\s*(?:en\s+)?(?:CHSB|BORG)?[^\n]{0,40}(?:parrainage|referral|filleul)",
            text,
        )
    if m:
        reward = normalize_reward(m.group(1))
        if reward and is_plausible_reward(reward):
            fields["referee_reward"] = reward
    dep = re.search(
        r"(?i)(?:parrainage|referral).{0,80}(?:dépôt|deposit|buy)[^\d]{0,30}(\d[\d\s.,]*\s*€)",
        text,
    )
    if dep:
        fields["min_deposit"] = normalize_reward(dep.group(1))
    if fields.get("referee_reward") and fields.get("min_deposit"):
        return _base(cfg, html, fields, Confidence.HIGH, ["swissborg_reward_and_min"])
    if fields.get("referee_reward"):
        return _base(cfg, html, fields, Confidence.HIGH, ["swissborg_reward"])
    # Homepage without referral block → not a false HIGH
    return _base(
        cfg,
        html,
        {},
        Confidence.REVIEW,
        ["swissborg_no_referral_block_on_page"],
        FailureCode.NO_PUBLIC_OFFER,
    )


def parse_paypal_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """PayPal: reward + spend_min + qualification_days as separate fields."""
    text = _text(html)
    fields: dict[str, str | None] = {}
    # e.g. 10 € ... achat minimum de 5 € ... 30 jours
    m = re.search(
        r"(?i)(?:recevez|gagnez|obtenez|get|earn)\s*(?:jusqu['’]à\s*)?(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?|de\s+crédit|credit)", text)
    if m:
        fields["referee_reward"] = normalize_reward(m.group(1))
    spend = re.search(
        r"(?i)(?:achat|paiement|spend|purchase)\s*(?:minimum|min\.?)?\s*(?:de\s*)?(\d[\d\s.,]*\s*€)",
        text,
    )
    if spend:
        fields["min_spend"] = normalize_reward(spend.group(1))
    days = re.search(r"(?i)(?:dans les|sous|within)\s*(\d+)\s*jours?", text)
    if days:
        fields["qualification_days"] = days.group(1)
    if fields.get("referee_reward") and (fields.get("min_spend") or fields.get("qualification_days")):
        return _base(cfg, html, fields, Confidence.HIGH, ["paypal_reward_conditions_split"])
    if fields.get("referee_reward"):
        # multi amounts without labels → REVIEW
        amounts = [normalize_reward(a) for a in re.findall(r"\d[\d\s.,]*\s*€", text)]
        amounts = [a for a in amounts if a]
        if len(set(amounts)) > 1 and not fields.get("min_spend"):
            return _base(cfg, html, fields, Confidence.REVIEW, ["paypal_multi_amount"], FailureCode.AMBIGUOUS_REWARD)
        return _base(cfg, html, fields, Confidence.HIGH, ["paypal_reward_only"])
    return parse_structured_first(html, cfg, offer)


def parse_airbnb_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    fields: dict[str, str | None] = {}
    # credit amounts often "X € de crédit voyage"
    m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:de\s+)?(?:crédit|credit|voyage|travel)", text)
    if not m:
        m = re.search(r"(?i)(?:jusqu['’]à|up to)\s*(\d[\d\s.,]*\s*€)", text)
    if m:
        fields["referee_reward"] = normalize_reward(m.group(1))
        fields["reward_type"] = "travel_credit"
    if fields.get("referee_reward"):
        return _base(cfg, html, fields, Confidence.HIGH, ["airbnb_travel_credit"])
    return parse_structured_first(html, cfg, offer)


def parse_coinbase_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    fields: dict[str, str | None] = {}
    m = re.search(r"(?i)(?:up to|jusqu['’]à)\s*(\$?\d[\d\s.,]*)\s*(?:in\s+)?(?:crypto|bitcoin|btc)?", text)
    m2 = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:en\s+)?(?:crypto|bitcoin)?", text)
    if m2:
        fields["referee_reward"] = normalize_reward(m2.group(1))
    elif m:
        fields["referee_reward"] = normalize_reward(m.group(1) + " €")
    trade = re.search(r"(?i)(?:trade|buy|acheter)[^\d]{0,40}(\$?\d[\d\s.,]*)", text)
    if trade:
        fields["trade_min"] = trade.group(1)
    if fields.get("referee_reward"):
        conf = Confidence.HIGH if fields.get("trade_min") else Confidence.REVIEW
        code = FailureCode.NONE if conf == Confidence.HIGH else FailureCode.AMBIGUOUS_REWARD
        return _base(cfg, html, fields, conf, ["coinbase_referral"], code)
    return parse_structured_first(html, cfg, offer)


def parse_bybit_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    fields: dict[str, str | None] = {}
    # Prefer structured/Next data (page is often JS shell)
    from lib.monitor.structured import extract_next_data, structured_reward_hints

    nd = extract_next_data(html)
    blob = str(nd) if nd is not None else html
    m = re.search(
        r"(?i)(?:commission|rebate|reward|bonus|fee\s*discount)[^\d%]{0,40}(\d{1,3}\s*%|\d[\d\s.,]*\s*USDT)",
        blob,
    )
    if not m:
        m = re.search(r"(?i)(?:commission|rebate|reward|bonus)[^\d%]{0,30}(\d+\s*%|\d[\d\s.,]*\s*USDT)", text)
    if m:
        fields["referee_reward"] = m.group(1).strip()
        fields["reward_type"] = "commission"
        return _base(cfg, html, fields, Confidence.HIGH, ["bybit_commission_or_bonus"], mode="structured_json" if nd else "html")
    pct = re.search(r"(?i)(\d+\s*%\s*(?:commission|rebate|fee))", text + " " + blob[:5000])
    if pct:
        fields["referee_reward"] = pct.group(1)
        fields["reward_type"] = "commission"
        return _base(cfg, html, fields, Confidence.HIGH, ["bybit_pct"])
    hints = structured_reward_hints(html)
    if hints.get("referee_reward"):
        fields["referee_reward"] = normalize_reward(hints["referee_reward"])
        return _base(cfg, html, fields, Confidence.REVIEW, ["bybit_structured_hint"], mode="structured_json")
    # Thin JS shell without public numbers
    if len(text) < 200:
        return _base(
            cfg,
            html,
            {},
            Confidence.REVIEW,
            ["bybit_js_shell_no_public_rate"],
            FailureCode.DYNAMIC_JS,
        )
    return parse_structured_first(html, cfg, offer)


def parse_vinted_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    m = re.search(r"(?i)(\d[\d\s.,]*\s*€)\s*(?:de\s+)?(?:crédit|credit|bon d['’]achat)", text)
    if m:
        reward = normalize_reward(m.group(1))
        if reward:
            return _base(cfg, html, {"referee_reward": reward, "reward_type": "credit"}, Confidence.HIGH, ["vinted_credit"])
    return parse_structured_first(html, cfg, offer)


def parse_totalenergies_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """Isolate parrainage bonus vs energy unit prices (1,99 €/kWh style)."""
    text = _text(html)
    fields: dict[str, str | None] = {}
    m = re.search(
        r"(?i)(?:parrainage|filleul|parrain)[^\d]{0,60}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(
            r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?)?[^\n]{0,40}(?:parrainage|filleul|parrain)",
            text,
        )
    if m:
        reward = normalize_reward(m.group(1))
        # reject tiny unit prices
        nums = re.findall(r"\d+", (reward or "").replace(" ", "").replace(",", "."))
        if reward and nums and int(float(nums[0])) >= 10:
            fields["referee_reward"] = reward
            fields["reward_type"] = "cash"
    if fields.get("referee_reward"):
        return _base(cfg, html, fields, Confidence.HIGH, ["totalenergies_parrainage_context"])
    return _base(
        cfg,
        html,
        {},
        Confidence.REVIEW,
        ["totalenergies_no_parrainage_block"],
        FailureCode.AMBIGUOUS_REWARD,
    )


def parse_acheel_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """Acheel: distinguish parrainage bonus from coverage ceilings (1000/20000 €)."""
    text = _text(html)
    fields: dict[str, str | None] = {}
    m = re.search(
        r"(?i)(?:parrainage|filleul|parrain|invitation)[^\d]{0,50}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(
            r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?)?[^\n]{0,30}(?:parrainage|à l['’]inscription)",
            text,
        )
    if m:
        reward = normalize_reward(m.group(1))
        nums = re.findall(r"\d+", (reward or "").replace(" ", ""))
        # coverage ceilings are typically ≥ 1000
        if reward and nums and 5 <= int(nums[0]) <= 200:
            fields["referee_reward"] = reward
            return _base(cfg, html, fields, Confidence.HIGH, ["acheel_parrainage_amount"])
    return _base(
        cfg,
        html,
        {},
        Confidence.REVIEW,
        ["acheel_ambiguous_coverage_vs_bonus"],
        FailureCode.AMBIGUOUS_REWARD,
    )


def parse_nrj_mobile_referral_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """NRJ Mobile: plan prices (9/15/17/29 €) vs parrainage bonus."""
    text = _text(html)
    fields: dict[str, str | None] = {}
    m = re.search(
        r"(?i)(?:parrainage|filleul|parrain)[^\d]{0,50}(\d[\d\s.,]*\s*€)",
        text,
    )
    if not m:
        m = re.search(
            r"(?i)(\d[\d\s.,]*\s*€)\s*(?:offerts?)[^\n]{0,40}(?:parrainage|filleul)",
            text,
        )
    if m:
        reward = normalize_reward(m.group(1))
        if reward and is_plausible_reward(reward):
            fields["referee_reward"] = reward
            return _base(cfg, html, fields, Confidence.HIGH, ["nrj_parrainage_context"])
    return _base(
        cfg,
        html,
        {},
        Confidence.REVIEW,
        ["nrj_plan_prices_not_parrainage"],
        FailureCode.AMBIGUOUS_REWARD,
    )


def parse_unibet_promos_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    text = _text(html)
    m = re.search(r"(?i)(?:parrainage|filleul)[^\d]{0,40}(\d[\d\s.,]*\s*€)", text)
    if not m:
        m = re.search(r"(?i)jusqu['’]à\s*(\d[\d\s.,]*\s*€)", text)
    if m:
        reward = normalize_reward(m.group(1))
        # promos page often many offers → REVIEW unless parrainage context
        conf = Confidence.HIGH if "parrain" in text.lower() else Confidence.REVIEW
        code = FailureCode.NONE if conf == Confidence.HIGH else FailureCode.AMBIGUOUS_REWARD
        return _base(cfg, html, {"referee_reward": reward}, conf, ["unibet_promo"], code)
    return parse_structured_first(html, cfg, offer)


def parse_boursobank_parrainage_fr(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """BoursoBank: multi-tier primes — model max + tiers, never pick insurance prices."""
    text = _text(html)
    fields: dict[str, str | None] = {}
    # Filleul max: "prime maximale de 160€" or "Jusqu'à 160€ offerts"
    m = re.search(
        r"(?i)(?:jusqu['’]à|prime maximale de)\s*(\d[\d\s.,]*)\s*€\s*(?:offerts?)?",
        text,
    )
    if m:
        fields["referee_reward"] = normalize_reward(f"jusqu'à {m.group(1)} €")
        fields["reward_type"] = "tiered_cash"
        fields["campaign_variant"] = "public_bon_plan"
    # min first deposit often 300€
    dep = re.search(r"(?i)1er versement d['’]un minimum de\s*(\d+)\s*€", text)
    if not dep:
        dep = re.search(r"(?i)premier versement[^\d]{0,40}(\d+)\s*€", text)
    if dep:
        fields["min_deposit"] = normalize_reward(f"{dep.group(1)} €")
    days = re.search(r"(?i)(?:sous|dans les)\s*(\d+)\s*jours?", text)
    if days:
        fields["qualification_days"] = days.group(1)
    if fields.get("referee_reward") and "160" in (fields["referee_reward"] or ""):
        return _base(
            cfg,
            html,
            fields,
            Confidence.HIGH,
            ["boursobank_max_prime_and_tiers"],
        )
    if fields.get("referee_reward"):
        return _base(cfg, html, fields, Confidence.REVIEW, ["boursobank_partial"], FailureCode.AMBIGUOUS_REWARD)
    return _base(
        cfg,
        html,
        {},
        Confidence.REVIEW,
        ["boursobank_no_clear_prime"],
        FailureCode.AMBIGUOUS_REWARD,
    )


def parse_app_personalized_stub(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    """Public page may describe program existence but amounts are app-personalized."""
    return _base(
        cfg,
        html,
        {},
        Confidence.REJECT,
        ["app_personalized_no_public_fixed_reward", "offer_kind=APP_PERSONALIZED"],
        FailureCode.APP_ONLY,
    )


def parse_operator_only_stub(html: str, cfg: SourceConfig, offer: dict | None = None) -> NormalizedOffer:
    return _base(
        cfg,
        html,
        {},
        Confidence.REJECT,
        ["operator_only_no_public_source"],
        FailureCode.NO_PUBLIC_OFFER,
    )


PROGRAM_PARSERS = {
    "igraal_referral_fr": parse_igraal_referral_fr,
    "cashback_portal_fr": parse_cashback_portal_fr,
    "ledger_referral_fr": parse_ledger_referral_fr,
    "winamax_parrainage_fr": parse_winamax_parrainage_fr,
    "kraken_referral_fr": parse_kraken_referral_fr,
    "swissborg_referral_fr": parse_swissborg_referral_fr,
    "paypal_referral_fr": parse_paypal_referral_fr,
    "airbnb_referral_fr": parse_airbnb_referral_fr,
    "coinbase_referral_fr": parse_coinbase_referral_fr,
    "bybit_referral_fr": parse_bybit_referral_fr,
    "vinted_referral_fr": parse_vinted_referral_fr,
    "totalenergies_referral_fr": parse_totalenergies_referral_fr,
    "unibet_promos_fr": parse_unibet_promos_fr,
    "boursobank_parrainage_fr": parse_boursobank_parrainage_fr,
    "acheel_referral_fr": parse_acheel_referral_fr,
    "nrj_mobile_referral_fr": parse_nrj_mobile_referral_fr,
    "app_personalized_stub": parse_app_personalized_stub,
    "operator_only_stub": parse_operator_only_stub,
}
