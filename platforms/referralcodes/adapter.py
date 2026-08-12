from platforms.text_sync_adapter import ManualPlatformAdapter


class ReferralCodesAdapter(ManualPlatformAdapter):
    """Audit: prioriser import/API officiel avant automation fragile."""

    platform_id = "referralcodes"
    capability = "MANUAL"
