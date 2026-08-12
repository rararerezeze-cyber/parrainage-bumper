from __future__ import annotations

from platforms.text_sync_adapter import TextSyncAdapter


class SuperParrainAdapter(TextSyncAdapter):
    platform_id = "super-parrain"
    capability = "AUTO"
