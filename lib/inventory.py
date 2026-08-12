from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lib.models import PlatformMapping
from lib.paths import MAPPINGS_DIR
from platforms.registry import ALL_PLATFORMS, get_adapter, platform_capability


@dataclass
class MappingRef:
    platform: str
    program: str
    language: str
    path: Path

    def load(self) -> PlatformMapping:
        with self.path.open(encoding="utf-8") as fh:
            return PlatformMapping.from_dict(json.load(fh))


def list_mapping_refs() -> list[MappingRef]:
    if not MAPPINGS_DIR.exists():
        return []
    refs: list[MappingRef] = []
    for path in sorted(MAPPINGS_DIR.glob("*.json")):
        # platform.program.language.json — platform may contain hyphens
        name = path.name[: -len(".json")]
        parts = name.split(".")
        if len(parts) < 3:
            continue
        language = parts[-1]
        program = parts[-2]
        platform = ".".join(parts[:-2])
        # Our filenames use super-parrain.kraken.fr → split on last two dots only
        # Actually format is {platform}.{program}.{language}.json with platform having hyphens not dots
        # e.g. super-parrain.kraken.fr.json → parts = [super-parrain, kraken, fr]
        if len(parts) == 3:
            platform, program, language = parts
        else:
            # fallback
            language = parts[-1]
            program = parts[-2]
            platform = ".".join(parts[:-2])
        refs.append(MappingRef(platform=platform, program=program, language=language, path=path))
    return refs


def list_platforms() -> list[dict]:
    out = []
    for pid in ALL_PLATFORMS:
        out.append(
            {
                "id": pid,
                "capability": platform_capability(pid),
            }
        )
    return out
