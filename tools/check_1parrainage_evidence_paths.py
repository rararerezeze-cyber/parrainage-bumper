#!/usr/bin/env python3
"""Fail-closed path policy for 1Parrainage evidence persistence."""
from __future__ import annotations

import json
import sys
from pathlib import PurePosixPath

PERSISTED_PATHS = (
    "data/captures/canary-1parrainage-kraken.json",
    "data/platform-write-status.json",
    "data/circuit-breakers.json",
)
TRANSIENT_PATHS = ("data/audit/events.jsonl",)


def _normalize(path: str) -> str:
    return PurePosixPath((path or "").replace("\\", "/")).as_posix()


def validate_unstaged_paths(paths: list[str]) -> dict[str, list[str]]:
    observed = sorted({_normalize(path) for path in paths if (path or "").strip()})
    transient = sorted(path for path in observed if path in TRANSIENT_PATHS)
    unexpected = sorted(path for path in observed if path not in TRANSIENT_PATHS)
    if unexpected:
        raise ValueError(
            "Unexpected unstaged paths after evidence staging: "
            + ", ".join(unexpected)
        )
    return {"observed": observed, "transient": transient, "unexpected": []}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        result = validate_unstaged_paths(args)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
