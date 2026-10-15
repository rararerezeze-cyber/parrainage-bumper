"""Persist a human rejection of a specific public-monitor candidate value."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from lib.paths import DATA_DIR

REJECTIONS_PATH = DATA_DIR / "monitor" / "rejected-candidates.json"


def _key(program, field, value):
    p = str(program).strip().lower()
    fld = str(field).strip().lower()
    val = re.sub(r"\s+", " ", str(value)).strip().casefold()
    if not p or not fld or not val or len(val) > 500:
        raise ValueError("invalid_monitor_rejection")
    return f"{p}|{fld}|{val}"


def _load(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("rejections"), list):
            return state
    except (ValueError, OSError):
        pass
    return {"version": 1, "rejections": []}


def is_rejected_candidate(program, field, value, *, path=None):
    try:
        key = _key(program, field, value)
    except ValueError:
        return False
    return any(isinstance(x, dict) and x.get("key") == key for x in _load(path or REJECTIONS_PATH)["rejections"])


def reject_candidate(program, field, value, *, path=None):
    dest = path or REJECTIONS_PATH
    key = _key(program, field, value)
    state = _load(dest)
    if not any(isinstance(x, dict) and x.get("key") == key for x in state["rejections"]):
        state["rejections"].append({"key": key, "rejected_at": datetime.now(timezone.utc).isoformat()})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(dest)
    if not is_rejected_candidate(program, field, value, path=dest):
        raise RuntimeError("reject_not_persisted")
    return {"program": program, "field": field, "value": value}
