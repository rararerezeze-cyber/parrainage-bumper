from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.paths import SYNC_STATE_PATH, sync_entry_key


class SyncStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or SYNC_STATE_PATH

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "updated_at": None, "entries": {}}
        with self.path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def get_entry(self, platform: str, program: str, language: str) -> dict[str, Any] | None:
        key = sync_entry_key(platform, program, language)
        return self.load().get("entries", {}).get(key)

    def upsert_entry(self, platform: str, program: str, language: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        entries = data.setdefault("entries", {})
        key = sync_entry_key(platform, program, language)
        entry = entries.get(key, {
            "platform": platform,
            "program": program,
            "language": language,
        })
        entry.update(patch)
        entries[key] = entry
        self.save(data)
        return entry
