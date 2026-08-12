from platforms.super_parrain.adapter import SuperParrainAdapter

ADAPTERS = {
    "super-parrain": SuperParrainAdapter,
}

MANUAL_REVIEW_PLATFORMS = frozenset({
    "referralcodes.com",
    "referraldrop.com",
    "1parrainage.com",
})


def get_adapter(platform: str) -> SuperParrainAdapter:
    if platform in MANUAL_REVIEW_PLATFORMS:
        raise ValueError(f"{platform}: manual_review_required — adapter non implemente")
    try:
        cls = ADAPTERS[platform]
    except KeyError as exc:
        raise ValueError(f"Plateforme inconnue: {platform}") from exc
    return cls()
