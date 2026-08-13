from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.paths import DATA_DIR

HISTORY_PATH = DATA_DIR / "monitor" / "history.jsonl"
LAST_OBS_PATH = DATA_DIR / "monitor" / "last-observations.json"


def load_last_observations() -> dict[str, Any]:
    if not LAST_OBS_PATH.exists():
        return {}
    try:
        return json.loads(LAST_OBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def append_history(entry: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(limit: int = 200) -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
