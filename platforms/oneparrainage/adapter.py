from platforms.text_sync_adapter import ManualPlatformAdapter


class OneParrainageAdapter(ManualPlatformAdapter):
    """Attention: modification contenu peut interagir avec logique de remontee."""

    platform_id = "1parrainage"
    capability = "MANUAL"
