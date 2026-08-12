from __future__ import annotations

from platforms.base import PlatformAdapter
from platforms.code_parrainage.adapter import CodeParrainageAdapter
from platforms.oneparrainage.adapter import OneParrainageAdapter
from platforms.parrainage_co.adapter import ParrainageCoAdapter
from platforms.referralcode_tv.adapter import ReferralCodeTvAdapter
from platforms.referralcodes.adapter import ReferralCodesAdapter
from platforms.referraldrop.adapter import ReferralDropAdapter
from platforms.super_parrain.adapter import SuperParrainAdapter
from platforms.text_sync_adapter import TextSyncAdapter

# Identifiants canoniques (7 plateformes)
ADAPTERS: dict[str, type[PlatformAdapter]] = {
    "super-parrain": SuperParrainAdapter,
    "parrainage-co": ParrainageCoAdapter,
    "code-parrainage": CodeParrainageAdapter,
    "referralcode-tv": ReferralCodeTvAdapter,
    "referralcodes": ReferralCodesAdapter,
    "referraldrop": ReferralDropAdapter,
    "1parrainage": OneParrainageAdapter,
}

# Alias historiques / domaines
ALIASES = {
    "super": "super-parrain",
    "super-parrain.com": "super-parrain",
    "parrainage": "parrainage-co",
    "parrainage.co": "parrainage-co",
    "code": "code-parrainage",
    "code-parrainage.net": "code-parrainage",
    "referralcode": "referralcode-tv",
    "referralcode.tv": "referralcode-tv",
    "referralcodes.com": "referralcodes",
    "referraldrop.com": "referraldrop",
    "1parrainage.com": "1parrainage",
}

ALL_PLATFORMS = list(ADAPTERS.keys())


def normalize_platform(platform: str) -> str:
    p = platform.strip().lower()
    return ALIASES.get(p, p)


def get_adapter(platform: str) -> PlatformAdapter:
    key = normalize_platform(platform)
    try:
        cls = ADAPTERS[key]
    except KeyError as exc:
        raise ValueError(f"Plateforme inconnue: {platform}") from exc
    return cls()


def platform_capability(platform: str) -> str:
    """AUTO | CAPTURE_PENDING | MANUAL — base + presence de mappings reels."""
    from lib.inventory import list_mapping_refs

    adapter = get_adapter(platform)
    base = getattr(adapter, "capability", "AUTO")
    if base == "MANUAL":
        return "MANUAL"
    has_mapping = any(r.platform == normalize_platform(platform) for r in list_mapping_refs())
    if not has_mapping:
        return "CAPTURE_PENDING"
    return base


def effective_capability(adapter: PlatformAdapter, platform: str) -> str:
    base = getattr(adapter, "capability", "AUTO")
    if base == "MANUAL":
        return "MANUAL"
    from lib.inventory import list_mapping_refs

    if not any(r.platform == platform for r in list_mapping_refs()):
        return "CAPTURE_PENDING"
    return base
