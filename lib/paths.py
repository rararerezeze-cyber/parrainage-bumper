from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OFFERS_PATH = DATA_DIR / "offers.json"
SYNC_STATE_PATH = DATA_DIR / "sync-state.json"
TEMPLATES_DIR = DATA_DIR / "platform-templates"
MAPPINGS_DIR = DATA_DIR / "platform-mappings"


def mapping_path(platform: str, program: str, language: str) -> Path:
    return MAPPINGS_DIR / f"{platform}.{program}.{language}.json"


def template_path(platform: str, program: str, language: str) -> Path:
    return TEMPLATES_DIR / platform / f"{program}.{language}.txt"


def golden_path(platform: str, program: str, language: str) -> Path:
    return TEMPLATES_DIR / platform / f"{program}.{language}.golden.txt"


def sync_entry_key(platform: str, program: str, language: str) -> str:
    return f"{platform}:{program}:{language}"
