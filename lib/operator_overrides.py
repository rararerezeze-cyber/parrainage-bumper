"""Operator overrides: PLATFORM > GLOBAL > ACCEPTED_MONITOR > CANONICAL.

Distinct from monitor observations. Deterministic, auditable, no secrets.
A public monitor must never overwrite these values.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import ACCEPTED_MONITOR_FIELDS_PATH, DATA_DIR, OPERATOR_OVERRIDES_PATH

# Logical fields pilotable via Telegram / operator
PILOTABLE_FIELDS = frozenset(
    {
        "personal_code",
        "personal_link",
        "referee_reward",
        "referrer_reward",
        "conditions",
        "min_deposit",
        "min_spend",
        "min_trade",
        "trade_min",
        "transaction_count",
        "qualification_days",
        "expiry_date",
        "reward_type",
        "title",
        # aliases kept as offer-side keys for convenience
        "code",
        "link",
        "reward",
        "cond",
    }
)

# Natural language / Telegram aliases → logical field
FIELD_ALIASES: dict[str, str] = {
    "code": "personal_code",
    "personal_code": "personal_code",
    "code parrain": "personal_code",
    "lien": "personal_link",
    "link": "personal_link",
    "personal_link": "personal_link",
    "gain filleul": "referee_reward",
    "filleul": "referee_reward",
    "reward": "referee_reward",
    "referee_reward": "referee_reward",
    "bonus filleul": "referee_reward",
    "gain parrain": "referrer_reward",
    "parrain": "referrer_reward",
    "referrer_reward": "referrer_reward",
    "conditions": "conditions",
    "cond": "conditions",
    "condition": "conditions",
    "depot minimum": "min_deposit",
    "dépôt minimum": "min_deposit",
    "min deposit": "min_deposit",
    "min_deposit": "min_deposit",
    "depot min": "min_deposit",
    "dépôt min": "min_deposit",
    "min spend": "min_spend",
    "min_spend": "min_spend",
    "depense minimum": "min_spend",
    "dépense minimum": "min_spend",
    "min trade": "trade_min",
    "min_trade": "trade_min",
    "trade min": "trade_min",
    "trade_min": "trade_min",
    "transaction": "transaction_count",
    "transactions": "transaction_count",
    "transaction_count": "transaction_count",
    "delai": "qualification_days",
    "délai": "qualification_days",
    "jours": "qualification_days",
    "qualification_days": "qualification_days",
    "date fin": "expiry_date",
    "expiry": "expiry_date",
    "expiry_date": "expiry_date",
    "echeance": "expiry_date",
    "échéance": "expiry_date",
    "reward_type": "reward_type",
    "type recompense": "reward_type",
    "title": "title",
    "titre": "title",
}

# Logical → offers.json key when applicable
LOGICAL_TO_OFFER: dict[str, str] = {
    "personal_code": "code",
    "personal_link": "link",
    "referee_reward": "reward",
    "conditions": "cond",
    "title": "name",
}

PLATFORM_ALIASES: dict[str, str] = {
    "super-parrain": "super-parrain",
    "super parrain": "super-parrain",
    "superparrain": "super-parrain",
    "parrainage.co": "parrainage-co",
    "parrainage-co": "parrainage-co",
    "parrainage co": "parrainage-co",
    "code-parrainage": "code-parrainage",
    "code parrainage": "code-parrainage",
    "1parrainage": "1parrainage",
    "1 parrainage": "1parrainage",
    "referralcodes": "referralcodes",
    "referralcodes.com": "referralcodes",
    "referralcode.tv": "referralcode-tv",
    "referralcode-tv": "referralcode-tv",
    "referraldrop": "referraldrop",
    "referraldrop.com": "referraldrop",
}

SOURCE_PLATFORM_OPERATOR = "PLATFORM_OPERATOR_OVERRIDE"
SOURCE_GLOBAL_OPERATOR = "GLOBAL_OPERATOR_OVERRIDE"
SOURCE_ACCEPTED_MONITOR = "ACCEPTED_PUBLIC_MONITOR_VALUE"
SOURCE_CANONICAL = "CANONICAL_DEFAULT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_field_name(raw: str) -> str | None:
    key = " ".join(raw.strip().lower().split())
    key = key.replace("_", " ")
    # try multi-word then single
    if key in FIELD_ALIASES:
        return FIELD_ALIASES[key]
    compact = key.replace(" ", "_")
    if compact in FIELD_ALIASES:
        return FIELD_ALIASES[compact]
    # direct logical
    if key.replace(" ", "_") in PILOTABLE_FIELDS:
        return key.replace(" ", "_")
    return FIELD_ALIASES.get(key)


def normalize_platform(raw: str | None) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    t = " ".join(str(raw).strip().lower().split())
    if t in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[t]
    t2 = t.replace(" ", "-")
    if t2 in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[t2]
    # already id?
    if t2 in set(PLATFORM_ALIASES.values()):
        return t2
    return None


def override_id(program: str, field: str, platform: str | None) -> str:
    if platform:
        return f"platform:{platform}:{program}:{field}"
    return f"global:{program}:{field}"


@dataclass
class OperatorOverride:
    program: str
    field: str
    value: str
    platform: str | None = None  # None = GLOBAL
    source: str = SOURCE_GLOBAL_OPERATOR
    created_at: str = ""
    updated_at: str = ""
    message: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = override_id(self.program, self.field, self.platform)
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.platform:
            self.source = SOURCE_PLATFORM_OPERATOR
        else:
            self.source = SOURCE_GLOBAL_OPERATOR

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OperatorOverride":
        return cls(
            program=d["program"],
            field=d["field"],
            value=str(d["value"]),
            platform=d.get("platform"),
            source=d.get("source") or SOURCE_GLOBAL_OPERATOR,
            created_at=d.get("created_at") or "",
            updated_at=d.get("updated_at") or "",
            message=d.get("message"),
            id=d.get("id") or "",
        )


@dataclass
class EffectiveValue:
    field: str
    value: str | None
    source: str
    program: str
    platform: str | None = None
    override_id: str | None = None


class OperatorOverrideStore:
    def __init__(self, path: Path | None = None):
        self.path = path or OPERATOR_OVERRIDES_PATH

    def load(self) -> list[OperatorOverride]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = raw.get("overrides") if isinstance(raw, dict) else raw
        out: list[OperatorOverride] = []
        for item in items or []:
            try:
                out.append(OperatorOverride.from_dict(item))
            except Exception:
                continue
        return out

    def save(self, overrides: list[OperatorOverride]) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now(),
            "note": (
                "Operator overrides (Telegram). Precedence: "
                "PLATFORM_OPERATOR > GLOBAL_OPERATOR > ACCEPTED_MONITOR > CANONICAL. "
                "Monitor observations must never overwrite these."
            ),
            "overrides": [o.to_dict() for o in sorted(overrides, key=lambda x: x.id)],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
        # Persistence confirmed: re-read before callers treat save as success
        verify = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(verify, dict) or "overrides" not in verify:
            raise RuntimeError("overrides_persist_unconfirmed")
        if len(verify.get("overrides") or []) != len(payload["overrides"]):
            raise RuntimeError("overrides_persist_count_mismatch")
        return self.path

    def upsert(
        self,
        program: str,
        field: str,
        value: str,
        *,
        platform: str | None = None,
        message: str | None = None,
    ) -> OperatorOverride:
        logical = normalize_field_name(field) or field
        if logical not in PILOTABLE_FIELDS and logical not in LOGICAL_TO_OFFER:
            # still allow if already normalized known
            if logical not in FIELD_ALIASES.values():
                raise ValueError(f"unknown_field:{field}")
        plat = normalize_platform(platform) if platform else None
        if platform and plat is None:
            raise ValueError(f"unknown_platform:{platform}")
        program = program.strip().lower()
        value = str(value).strip()
        if not value:
            raise ValueError("empty_value")
        # injection hardening
        if "\x00" in value or len(value) > 2000:
            raise ValueError("invalid_value")

        items = self.load()
        oid = override_id(program, logical, plat)
        now = _now()
        existing = next((o for o in items if o.id == oid), None)
        if existing:
            existing.value = value
            existing.updated_at = now
            existing.message = message
            ov = existing
        else:
            ov = OperatorOverride(
                program=program,
                field=logical,
                value=value,
                platform=plat,
                created_at=now,
                updated_at=now,
                message=message,
            )
            items.append(ov)
        self.save(items)
        return ov

    def remove(
        self,
        program: str,
        field: str,
        *,
        platform: str | None = None,
    ) -> bool:
        logical = normalize_field_name(field) or field
        plat = normalize_platform(platform) if platform else None
        if platform and plat is None:
            raise ValueError(f"unknown_platform:{platform}")
        oid = override_id(program.strip().lower(), logical, plat)
        items = self.load()
        new_items = [o for o in items if o.id != oid]
        if len(new_items) == len(items):
            return False
        self.save(new_items)
        return True

    def list_for_program(self, program: str, platform: str | None = None) -> list[OperatorOverride]:
        program = program.strip().lower()
        items = [o for o in self.load() if o.program == program]
        if platform is not None:
            plat = normalize_platform(platform)
            items = [o for o in items if o.platform == plat]
        return items


def load_accepted_monitor_fields() -> dict[str, dict[str, str]]:
    """program → field → value (accepted public monitor, never auto from HIGH alone)."""
    p = ACCEPTED_MONITOR_FIELDS_PATH
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(raw.get("programs") or {})


def resolve_effective_value(
    program: str,
    field: str,
    *,
    platform: str | None = None,
    canonical: str | None = None,
    store: OperatorOverrideStore | None = None,
    accepted: dict[str, dict[str, str]] | None = None,
) -> EffectiveValue:
    """Precedence: PLATFORM > GLOBAL > ACCEPTED_MONITOR > CANONICAL."""
    logical = normalize_field_name(field) or field
    store = store or OperatorOverrideStore()
    program = program.strip().lower()
    plat = normalize_platform(platform) if platform else None

    items = store.load()
    if plat:
        for o in items:
            if o.program == program and o.field == logical and o.platform == plat:
                return EffectiveValue(
                    field=logical,
                    value=o.value,
                    source=SOURCE_PLATFORM_OPERATOR,
                    program=program,
                    platform=plat,
                    override_id=o.id,
                )
    for o in items:
        if o.program == program and o.field == logical and o.platform is None:
            return EffectiveValue(
                field=logical,
                value=o.value,
                source=SOURCE_GLOBAL_OPERATOR,
                program=program,
                platform=plat,
                override_id=o.id,
            )

    accepted = accepted if accepted is not None else load_accepted_monitor_fields()
    mon = (accepted.get(program) or {}).get(logical)
    if mon is not None and str(mon).strip() != "":
        return EffectiveValue(
            field=logical,
            value=str(mon),
            source=SOURCE_ACCEPTED_MONITOR,
            program=program,
            platform=plat,
        )

    return EffectiveValue(
        field=logical,
        value=canonical,
        source=SOURCE_CANONICAL,
        program=program,
        platform=plat,
    )


def apply_effective_to_offer(
    offer: dict[str, Any],
    *,
    platform: str | None = None,
    store: OperatorOverrideStore | None = None,
) -> dict[str, Any]:
    """Return a copy of offer with GLOBAL/PLATFORM operator fields applied to offer keys."""
    out = dict(offer)
    program = str(offer.get("lk") or "")
    if not program:
        return out
    store = store or OperatorOverrideStore()
    accepted = load_accepted_monitor_fields()
    for logical, offer_key in LOGICAL_TO_OFFER.items():
        canon = out.get(offer_key)
        eff = resolve_effective_value(
            program,
            logical,
            platform=platform,
            canonical=str(canon) if canon is not None else None,
            store=store,
            accepted=accepted,
        )
        if eff.value is not None and eff.source != SOURCE_CANONICAL:
            out[offer_key] = eff.value
    # Extra fields not in offers.json: attach under _operator_extra for renderers
    extra: dict[str, str] = {}
    for logical in (
        "referrer_reward",
        "min_deposit",
        "min_spend",
        "trade_min",
        "min_trade",
        "transaction_count",
        "qualification_days",
        "expiry_date",
        "reward_type",
    ):
        eff = resolve_effective_value(
            program, logical, platform=platform, canonical=None, store=store, accepted=accepted
        )
        if eff.value is not None and eff.source != SOURCE_CANONICAL:
            extra[logical] = eff.value
    if extra:
        out["_operator_extra"] = extra
    return out


def effective_variables_for_mapping(
    mapping: Any,
    offer: dict[str, Any],
    *,
    store: OperatorOverrideStore | None = None,
    local_overrides: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    """Build render variables with full precedence for a platform mapping."""
    store = store or OperatorOverrideStore()
    accepted = load_accepted_monitor_fields()
    local_overrides = local_overrides or {}
    variables: dict[str, str | None] = {}
    program = mapping.program
    platform = mapping.platform

    published = getattr(mapping, "platform_values", None) or {}

    def _canonical_for(logical_name: str, offer_field: str | None) -> str | None:
        if logical_name in local_overrides:
            return local_overrides[logical_name]
        # Published identity stays the render canonical (catalog URL/code may differ).
        if logical_name in {"personal_code", "personal_link"}:
            pub = published.get(logical_name)
            if pub is not None and str(pub).strip() != "":
                return str(pub)
        if offer_field:
            raw = offer.get(offer_field)
            if raw is not None and str(raw).strip() != "":
                return str(raw)
        pub = published.get(logical_name)
        return str(pub) if pub is not None and str(pub).strip() != "" else None

    for logical_name, offer_field in (mapping.offer_fields or {}).items():
        eff = resolve_effective_value(
            program,
            logical_name,
            platform=platform,
            canonical=_canonical_for(logical_name, offer_field),
            store=store,
            accepted=accepted,
        )
        variables[logical_name] = eff.value

    # mutable fields that might only exist as operator extras / native spans
    for logical_name in mapping.mutable_fields or []:
        if logical_name in variables:
            continue
        eff = resolve_effective_value(
            program,
            logical_name,
            platform=platform,
            canonical=_canonical_for(logical_name, None),
            store=store,
            accepted=accepted,
        )
        variables[logical_name] = eff.value

    if published:
        from lib.native_field_format import adapt_monitor_value_to_native

        for logical_name, current in list(variables.items()):
            native = published.get(logical_name)
            if native is None:
                continue
            variables[logical_name] = adapt_monitor_value_to_native(
                logical_name, current, native
            )
    return variables


def status_report(program: str, store: OperatorOverrideStore | None = None) -> dict[str, Any]:
    store = store or OperatorOverrideStore()
    from lib.offers import OffersRepository

    offers = OffersRepository()
    try:
        offer = offers.get_by_slug(program)
    except KeyError:
        return {"error": "unknown_program", "program": program}

    overrides = store.list_for_program(program)
    fields = {}
    for logical, offer_key in LOGICAL_TO_OFFER.items():
        eff = resolve_effective_value(
            program, logical, canonical=str(offer.get(offer_key) or "") or None, store=store
        )
        fields[logical] = {
            "effective": eff.value,
            "source": eff.source,
            "canonical": offer.get(offer_key),
        }
    for o in overrides:
        if o.field not in fields:
            fields[o.field] = {
                "effective": o.value,
                "source": o.source,
                "canonical": None,
                "platform": o.platform,
            }
    return {
        "program": program,
        "overrides": [o.to_dict() for o in overrides],
        "fields": fields,
    }
