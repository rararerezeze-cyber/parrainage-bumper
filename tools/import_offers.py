#!/usr/bin/env python3
"""Copie offers.json depuis BonusParrain vers data/offers.json."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(
    r"c:\Users\adrie\OneDrive\Documents\Projets\Sites web\Mon site de parrainage\data\offers.json"
)
TARGET = ROOT / "data" / "offers.json"


def main() -> int:
    source = DEFAULT_SOURCE
    if not source.exists():
        print(f"Source introuvable: {source}")
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, TARGET)
    print(f"Copie OK: {source} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
