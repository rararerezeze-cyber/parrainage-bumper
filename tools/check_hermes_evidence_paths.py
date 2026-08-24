#!/usr/bin/env python3
"""Fail-closed residual-path policy for Hermes result persistence."""
from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath


TRANSIENT_TRACKED_PATHS = ("data/audit/events.jsonl",)

# Runner-only observability. gitignored, never committed, never business state:
# a BEST_EFFORT notification must not turn a successful mutation into a red
# workflow through the residual-path gate.
TRANSIENT_UNTRACKED_PREFIXES = ("data/notifications/",)


def _is_transient_untracked(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in TRANSIENT_UNTRACKED_PREFIXES)


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
        [path for path in observed_unstaged if path in TRANSIENT_TRACKED_PATHS]
        + [path for path in observed_untracked if _is_transient_untracked(path)]
    )
    unexpected = sorted(
        path
        for path in observed_unstaged
        if path not in TRANSIENT_TRACKED_PATHS
    ) + [path for path in observed_untracked if not _is_transient_untracked(path)]
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
