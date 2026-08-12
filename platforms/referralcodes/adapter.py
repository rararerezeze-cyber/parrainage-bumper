from platforms.text_sync_adapter import ManualPlatformAdapter


class ReferralCodesAdapter(ManualPlatformAdapter):
    """ReferralCodes.com (AVEC S) — prioriser Agent Import / API officielle.

    Ne jamais confondre avec referralcode.tv (secrets REFERRALCODE_* sans S).
    """

    platform_id = "referralcodes"
    capability = "MANUAL"
    prefer_official_import = True
