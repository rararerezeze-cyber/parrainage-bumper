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
    BROKEN = "BROKEN"
    UNCONFIGURED = "UNCONFIGURED"


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def monitor_may_write_field(self, field: str) -> bool:
        """Whether official monitor has authority over this field (policy only; no auto-accept yet)."""
        src = (self.field_sources or {}).get(field) or DEFAULT_FIELD_SOURCES.get(field)
        return src == "OFFICIAL_PUBLIC_MONITOR"


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
    high_streak: int = 0
    impact_count: int = 0

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
            "impact_count": self.impact_count,
        }


def _norm_value(s: str) -> str:
    s = s.lower().strip()
    s = " ".join(s.split())
    s = s.replace("€", "€").replace("eur", "€")
    s = s.replace("\u202f", " ").replace("\xa0", " ")
    return s
