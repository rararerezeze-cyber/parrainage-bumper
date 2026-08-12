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


# Business fields the monitor may extract (never personal codes/links)
BUSINESS_FIELDS = (
    "referee_reward",
    "referrer_reward",
    "conditions",
    "min_deposit",
    "min_spend",
    "deadline",
    "geo_restriction",
    "campaign_name",
)


@dataclass
class SourceConfig:
    program: str
    source_url: str | None
    source_type: str  # api | official_page | structured_json | html | manual | unmonitorable
    extraction_method: str
    fields_supported: list[str] = field(default_factory=list)
    locale: str = "fr"
    auth_required: bool = False
    parser: str = "generic_reward_html"
    confidence_default: str = "REVIEW"
    notes: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedOffer:
    program: str
    fields: dict[str, str | None] = field(default_factory=dict)
    confidence: Confidence = Confidence.REVIEW
    parser: str = ""
    source_url: str | None = None
    raw_fingerprint: str = ""
    notes: list[str] = field(default_factory=list)

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
    html_changed_hint: bool = False  # True if page hash differs but business same

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
        }


def _norm_value(s: str) -> str:
    s = s.lower().strip()
    s = " ".join(s.split())
    # normalize euro spacing
    s = s.replace("€", "€").replace("eur", "€")
    s = s.replace("\u202f", " ").replace("\xa0", " ")
    return s
