from platforms.text_sync_adapter import ManualPlatformAdapter


class ReferralDropAdapter(ManualPlatformAdapter):
    """Compte Google Sign-In — auth ecrase; lecture publique possible."""

    platform_id = "referraldrop"
    capability = "MANUAL"
    auth_status = "AUTH_BLOCKED_GOOGLE"
