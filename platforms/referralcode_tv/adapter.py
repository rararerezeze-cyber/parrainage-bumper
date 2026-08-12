from platforms.text_sync_adapter import TextSyncAdapter


class ReferralCodeTvAdapter(TextSyncAdapter):
    platform_id = "referralcode-tv"
    capability = "AUTO"  # CAPTURE_PENDING si 0 mapping
