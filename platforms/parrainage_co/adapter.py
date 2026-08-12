from platforms.text_sync_adapter import TextSyncAdapter


class ParrainageCoAdapter(TextSyncAdapter):
    """Capability effective = CAPTURE_PENDING tant qu'aucun mapping n'existe."""

    platform_id = "parrainage-co"
    capability = "AUTO"  # devient CAPTURE_PENDING via registry si 0 mapping
