#!/usr/bin/env python3
"""Fail-closed residual-path policy for Hermes result persistence."""
from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath


TRANSIENT_TRACKED_PATHS = ("data/audit/events.jsonl",)


def _normalize(path: str) -> str:
    return PurePosixPath((path or "").replace("\\", "/")).as_posix()


def validate_remaining_paths(
    *, unstaged: list[str], untracked: list[str]
) -> dict[str, list[str]]:
    observed_unstaged = sorted(
        {_normalize(path) for path in unstaged if (path or "").strip()}
    )
    observed_untracked = sorted(
        {_normalize(path) for path in untracked if (path or "").strip()}
    )
    transient = sorted(
        path for path in observed_unstaged if path in TRANSIENT_TRACKED_PATHS
    )
    unexpected = sorted(
        path
        for path in observed_unstaged
        if path not in TRANSIENT_TRACKED_PATHS
    ) + observed_untracked
    if unexpected:
        raise ValueError(
            "Unexpected residual paths after Hermes evidence staging: "
            + ", ".join(sorted(set(unexpected)))
        )
    return {
        "unstaged": observed_unstaged,
        "untracked": observed_untracked,
        "transient": transient,
        "unexpected": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unstaged", nargs="*", default=[])
    parser.add_argument("--untracked", nargs="*", default=[])
    args = parser.parse_args(argv)
    try:
        result = validate_remaining_paths(
            unstaged=args.unstaged,
            untracked=args.untracked,
        )
    except ValueError as exc:
        print(f"::error::{exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
