from platforms.text_sync_adapter import TextSyncAdapter


class CodeParrainageAdapter(TextSyncAdapter):
    platform_id = "code-parrainage"
    capability = "AUTO"  # CAPTURE_PENDING si 0 mapping
