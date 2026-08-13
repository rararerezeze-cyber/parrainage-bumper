from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Confidence(str, Enum):
    HIGH = "HIGH"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class ObservationStatus(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    CANDIDATE = "CANDIDATE"
    REVIEW = "REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class FailureCode(str, Enum):
    NONE = "NONE"
    DEAD_URL = "DEAD_URL"
    ANTIBOT_403 = "403_ANTIBOT"
    RATE_LIMIT = "RATE_LIMIT"
    APP_ONLY = "APP_ONLY"
    AMBIGUOUS_REWARD = "AMBIGUOUS_REWARD"
    NO_PUBLIC_OFFER = "NO_PUBLIC_OFFER"
    DYNAMIC_JS = "DYNAMIC_JS"
    WRONG_LOCALE = "WRONG_LOCALE"
    PARSER_MISMATCH = "PARSER_MISMATCH"
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    CHALLENGE = "CHALLENGE"
    EMPTY_PAGE = "EMPTY_PAGE"
    FETCH_FAILED = "FETCH_FAILED"
    WRONG_OR_DEAD_URL = "WRONG_OR_DEAD_URL"
    NO_FR_AUTHORITY = "NO_FR_AUTHORITY"


class SourceClass(str, Enum):
    VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
    OFFICIAL_BUT_NOT_MACHINE_READABLE = "OFFICIAL_BUT_NOT_MACHINE_READABLE"
    WRONG_OR_DEAD_URL = "WRONG_OR_DEAD_URL"
    AUTH_APP_ONLY = "AUTH_APP_ONLY"
    ANTI_BOT_BLOCKED = "ANTI_BOT_BLOCKED"
    NO_PUBLIC_REFERRAL_SOURCE = "NO_PUBLIC_REFERRAL_SOURCE"
    UNVERIFIED = "UNVERIFIED"


class OfferKind(str, Enum):
    PUBLIC_STABLE = "PUBLIC_STABLE"
    PUBLIC_CAMPAIGN = "PUBLIC_CAMPAIGN"
    APP_PERSONALIZED = "APP_PERSONALIZED"
    OPERATOR_ONLY = "OPERATOR_ONLY"


class MonitorProgramStatus(str, Enum):
    MONITOR_VERIFIED = "MONITOR_VERIFIED"
    PUBLIC_MONITORABLE_PENDING = "PUBLIC_MONITORABLE_PENDING"
    APP_PERSONALIZED = "APP_PERSONALIZED"
    OPERATOR_ONLY = "OPERATOR_ONLY"
    ANTI_BOT_BLOCKED = "ANTI_BOT_BLOCKED"
    NO_PUBLIC_REFERRAL_SOURCE = "NO_PUBLIC_REFERRAL_SOURCE"
    BROKEN = "BROKEN"
    UNCONFIGURED = "UNCONFIGURED"


# Final statuses (not "still researching")
FINAL_MONITOR_STATUSES = frozenset(
    {
        MonitorProgramStatus.MONITOR_VERIFIED.value,
        MonitorProgramStatus.APP_PERSONALIZED.value,
        MonitorProgramStatus.OPERATOR_ONLY.value,
        MonitorProgramStatus.ANTI_BOT_BLOCKED.value,
        MonitorProgramStatus.NO_PUBLIC_REFERRAL_SOURCE.value,
        MonitorProgramStatus.BROKEN.value,
    }
)


# Business fields the monitor may extract (never personal codes/links)
BUSINESS_FIELDS = (
    "referee_reward",
    "referrer_reward",
    "conditions",
    "min_deposit",
    "min_spend",
    "trade_min",
    "transaction_count",
    "qualification_days",
    "expiry_date",
    "reward_type",
    "geographic_scope",
    "campaign_variant",
    "deadline",
    "geo_restriction",
    "campaign_name",
)

# Fields that can change public announcements (mutable public)
PUBLIC_MUTABLE_FIELDS = (
    "referee_reward",
    "referrer_reward",
    "conditions",
    "min_deposit",
    "min_spend",
    "trade_min",
    "qualification_days",
    "expiry_date",
    "reward_type",
)


# Default field authority
DEFAULT_FIELD_SOURCES = {
    "personal_code": "TELEGRAM_OPERATOR",
    "personal_link": "TELEGRAM_OPERATOR",
    "referee_reward": "OFFICIAL_PUBLIC_MONITOR",
    "referrer_reward": "OFFICIAL_PUBLIC_MONITOR",
    "conditions": "OFFICIAL_PUBLIC_MONITOR",
    "min_deposit": "OFFICIAL_PUBLIC_MONITOR",
    "min_spend": "OFFICIAL_PUBLIC_MONITOR",
    "trade_min": "OFFICIAL_PUBLIC_MONITOR",
    "transaction_count": "OFFICIAL_PUBLIC_MONITOR",
    "qualification_days": "OFFICIAL_PUBLIC_MONITOR",
    "expiry_date": "OFFICIAL_PUBLIC_MONITOR",
    "reward_type": "OFFICIAL_PUBLIC_MONITOR",
    "geographic_scope": "OFFICIAL_PUBLIC_MONITOR",
    "campaign_variant": "OFFICIAL_PUBLIC_MONITOR",
}

# Documentary foreign source: may inform, never FR SoT for amounts
FS_FOREIGN_DOC = {
    **DEFAULT_FIELD_SOURCES,
    "referee_reward": "OPERATOR",
    "referrer_reward": "OPERATOR",
    "min_deposit": "OPERATOR",
    "min_spend": "OPERATOR",
    "trade_min": "OPERATOR",
    "qualification_days": "OPERATOR",
    "expiry_date": "OPERATOR",
    "conditions": "OPERATOR",  # conditions also geo-specific unless proven identical
}

FS_APP = {
    **DEFAULT_FIELD_SOURCES,
    "referee_reward": "OPERATOR",
    "referrer_reward": "OPERATOR",
    "conditions": "OFFICIAL_PUBLIC_MONITOR",
}

FS_OPERATOR_REWARD = {
    **DEFAULT_FIELD_SOURCES,
    "referee_reward": "OPERATOR",
    "referrer_reward": "OPERATOR",
    "conditions": "OPERATOR",
}


@dataclass
class SourceConfig:
    program: str
    source_url: str | None
    source_type: str  # api | structured_json | official_page | html | manual | unmonitorable
    extraction_method: str
    fields_supported: list[str] = field(default_factory=list)
    locale: str = "fr"
    auth_required: bool = False
    parser: str = "generic_reward_html"
    confidence_default: str = "REVIEW"
    notes: str | None = None
    enabled: bool = True
    source_class: str = SourceClass.UNVERIFIED.value
    offer_kind: str = OfferKind.PUBLIC_CAMPAIGN.value
    field_sources: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FIELD_SOURCES))
    structured_endpoint: str | None = None
    last_verify_http: int | None = None
    last_verify_at: str | None = None
    impact_count: int = 0
    # Locale / campaign scoping (canonical ads are FR)
    source_country: str = "FR"
    source_locale: str = "fr"
    canonical_country: str = "FR"
    campaign_scope: str = "FR"  # FR | EU | GLOBAL | US | UK | APP
    # Per-field FR authority override: field -> bool (True = may drive FR canonical)
    field_authority_fr: dict[str, bool] = field(default_factory=dict)
    # Explicit final classification when research concluded (optional)
    final_status: str | None = None
    parser_tests_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def monitor_may_write_field(self, field_name: str) -> bool:
        """Whether official monitor has authority over this field (policy only; no auto-accept yet)."""
        src = (self.field_sources or {}).get(field_name) or DEFAULT_FIELD_SOURCES.get(field_name)
        if src != "OFFICIAL_PUBLIC_MONITOR":
            return False
        # Locale gate: foreign sources need explicit FR authority
        if not self.has_fr_authority(field_name):
            return False
        return True

    def has_fr_authority(self, field_name: str) -> bool:
        """Can this source authoritatively set a FR canonical field?"""
        if field_name in (self.field_authority_fr or {}):
            return bool(self.field_authority_fr[field_name])
        # Default: same country/campaign as FR canonical
        sc = (self.source_country or "").upper()
        cc = (self.canonical_country or "FR").upper()
        scope = (self.campaign_scope or "").upper()
        if sc == cc:
            return True
        if scope in {"GLOBAL", "EU"} and field_name in {"reward_type", "geographic_scope"}:
            return True
        # UK/US/international amounts do not drive FR by default
        return False

    def mutable_public_field_count(self) -> int:
        return sum(1 for f in self.fields_supported if f in PUBLIC_MUTABLE_FIELDS)

    def priority_score(self) -> int:
        return int(self.impact_count) * max(1, self.mutable_public_field_count())


@dataclass
class NormalizedOffer:
    program: str
    fields: dict[str, str | None] = field(default_factory=dict)
    confidence: Confidence = Confidence.REVIEW
    parser: str = ""
    source_url: str | None = None
    raw_fingerprint: str = ""
    notes: list[str] = field(default_factory=list)
    failure_code: FailureCode = FailureCode.NONE
    extraction_mode: str = "html"  # html | structured_json | api

    def business_fingerprint(self) -> str:
        parts = []
        for k in sorted(self.fields.keys()):
            v = self.fields.get(k)
            if v is None or str(v).strip() == "":
                continue
            parts.append(f"{k}={_norm_value(str(v))}")
        return "|".join(parts)


@dataclass
class FieldChange:
    field: str
    old: str | None
    new: str | None


@dataclass
class Observation:
    program: str
    status: ObservationStatus
    confidence: Confidence
    source_url: str | None
    parser: str
    detected_at: str
    canonical_fields: dict[str, str | None]
    observed_fields: dict[str, str | None]
    changes: list[FieldChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    business_fingerprint: str = ""
    html_changed_hint: bool = False
    failure_code: FailureCode = FailureCode.NONE
    source_class: str = SourceClass.UNVERIFIED.value
    offer_kind: str = OfferKind.PUBLIC_CAMPAIGN.value
    monitor_status: str = MonitorProgramStatus.PUBLIC_MONITORABLE_PENDING.value
    high_streak: int = 0  # legacy alias = live_high_streak when live
    impact_count: int = 0
    # V2 fields
    live_high_streak: int = 0
    fixture_high_streak: int = 0
    parser_tests_passed: bool = False
    source_country: str = "FR"
    source_locale: str = "fr"
    campaign_scope: str = "FR"
    field_authority: dict[str, str] = field(default_factory=dict)
    consecutive_fetch_failures: int = 0
    last_success_at: str | None = None
    is_live: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "source_url": self.source_url,
            "parser": self.parser,
            "detected_at": self.detected_at,
            "canonical_fields": self.canonical_fields,
            "observed_fields": self.observed_fields,
            "changes": [asdict(c) for c in self.changes],
            "notes": self.notes,
            "error": self.error,
            "business_fingerprint": self.business_fingerprint,
            "html_changed_hint": self.html_changed_hint,
            "failure_code": self.failure_code.value,
            "source_class": self.source_class,
            "offer_kind": self.offer_kind,
            "monitor_status": self.monitor_status,
            "high_streak": self.high_streak,
            "live_high_streak": self.live_high_streak,
            "fixture_high_streak": self.fixture_high_streak,
            "parser_tests_passed": self.parser_tests_passed,
            "impact_count": self.impact_count,
            "source_country": self.source_country,
            "source_locale": self.source_locale,
            "campaign_scope": self.campaign_scope,
            "field_authority": self.field_authority,
            "consecutive_fetch_failures": self.consecutive_fetch_failures,
            "last_success_at": self.last_success_at,
            "is_live": self.is_live,
        }


def _norm_value(s: str) -> str:
    s = s.lower().strip()
    s = " ".join(s.split())
    s = s.replace("€", "€").replace("eur", "€")
    s = s.replace("\u202f", " ").replace("\xa0", " ")
    return s
