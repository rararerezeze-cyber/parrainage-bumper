from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.models import PlatformMapping
from lib.offers import OffersRepository
from lib.paths import mapping_path, template_path, golden_path


class MappingRepository:
    def load(self, platform: str, program: str, language: str) -> PlatformMapping:
        path = mapping_path(platform, program, language)
        if not path.exists():
            raise FileNotFoundError(f"Mapping introuvable: {path}")
        with path.open(encoding="utf-8") as fh:
            return PlatformMapping.from_dict(json.load(fh))


class TemplateRepository:
    def load_text(self, platform: str, program: str, language: str) -> str:
        path = template_path(platform, program, language)
        if not path.exists():
            raise FileNotFoundError(f"Template introuvable: {path}")
        return path.read_text(encoding="utf-8")

    def load_golden(self, platform: str, program: str, language: str) -> str:
        path = golden_path(platform, program, language)
        if not path.exists():
            raise FileNotFoundError(f"Golden introuvable: {path}")
        return path.read_text(encoding="utf-8")

    def exists(self, platform: str, program: str, language: str) -> bool:
        return template_path(platform, program, language).exists()

    def golden_exists(self, platform: str, program: str, language: str) -> bool:
        return golden_path(platform, program, language).exists()


class Renderer:
    def __init__(self, offers: OffersRepository | None = None):
        self.offers = offers or OffersRepository()

    def build_variables(
        self,
        mapping: PlatformMapping,
        offer: dict[str, Any] | None = None,
        overrides: dict[str, str | None] | None = None,
    ) -> dict[str, str | None]:
        """Build variables with operator precedence:

        PLATFORM_OPERATOR > GLOBAL_OPERATOR > ACCEPTED_MONITOR > local overrides > CANONICAL
        """
        from lib.operator_overrides import effective_variables_for_mapping

        offer = offer or self.offers.get_by_slug(mapping.program)
        return effective_variables_for_mapping(
            mapping,
            offer,
            local_overrides=overrides,
        )

    def render(
        self,
        template: str,
        mapping: PlatformMapping,
        offer: dict[str, Any] | None = None,
        overrides: dict[str, str | None] | None = None,
    ) -> str:
        variables = self.build_variables(mapping, offer=offer, overrides=overrides)
        rendered = template
        # Remplacement deterministe : seulement champs mutables declares.
        for field_name in mapping.mutable_fields:
            marker = mapping.markers.get(field_name)
            if not marker:
                raise ValueError(f"Marqueur manquant pour {field_name}")
            value = variables.get(field_name)
            if value is None:
                raise ValueError(f"Valeur manquante pour champ mutable {field_name}")
            rendered = rendered.replace(marker, value)
        self._assert_no_unresolved_markers(rendered, mapping)
        return rendered

    @staticmethod
    def _assert_no_unresolved_markers(text: str, mapping: PlatformMapping) -> None:
        unresolved = [
            name
            for name in mapping.mutable_fields
            if (marker := mapping.markers.get(name)) and marker in text
        ]
        if unresolved:
            raise ValueError(
                "Marqueurs mutables non resolus apres rendu: "
                + ", ".join(unresolved)
            )

    def validate_golden(
        self,
        historical_text: str,
        template: str,
        mapping: PlatformMapping,
        offer: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, str | None]]:
        rendered = self.render(template, mapping, offer=offer)
        variables = self.build_variables(mapping, offer=offer)
        return rendered == historical_text, rendered, variables
