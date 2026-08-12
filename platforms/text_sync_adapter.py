"""Adaptateur generique dry-run pour plateformes basees sur templates texte."""
from __future__ import annotations

from typing import Any

from lib.models import DryRunResult, PlatformMapping
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.sync_state import SyncStateStore
from lib.template_builder import extract_values_via_template
from platforms.base import PlatformAdapter


class TextSyncAdapter(PlatformAdapter):
    """Dry-run: compare texte historique (golden) vs rendu offers.json.

    - in_sync: identique
    - pending_update: diff de valeurs (normal, a synchroniser plus tard)
    - missing_source / render_error: probleme technique
    - manual: plateforme non automatisable
    """

    platform_id = "generic"
    capability = "AUTO"  # AUTO | REVIEW | MANUAL

    def __init__(
        self,
        mappings: MappingRepository | None = None,
        templates: TemplateRepository | None = None,
        renderer: Renderer | None = None,
        offers: OffersRepository | None = None,
        sync_state: SyncStateStore | None = None,
    ):
        self.mappings = mappings or MappingRepository()
        self.templates = templates or TemplateRepository()
        self.renderer = renderer or Renderer(offers=offers or OffersRepository())
        self.sync_state = sync_state or SyncStateStore()

    def dry_run(self, mapping: PlatformMapping) -> DryRunResult:
        # MANUAL sans template: statut manuel informatif
        if self.capability == "MANUAL" and (
            mapping.template_status == "missing_source"
            or not self.templates.exists(
                mapping.platform, mapping.program, mapping.language
            )
        ):
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode="manual_review_required",
                status="manual",
                historical_text=None,
                rendered_text=None,
                error="manual_review_required — pas de publication auto",
                blocking=False,
            )

        if mapping.template_status == "missing_source" or not self.templates.exists(
            mapping.platform, mapping.program, mapping.language
        ):
            error = (
                "missing_source: template absent pour "
                f"{mapping.platform}/{mapping.program}.{mapping.language}"
            )
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode=mapping.sync_mode,
                status="missing_source",
                historical_text=None,
                rendered_text=None,
                error=error,
                blocking=False,
            )

        template = self.templates.load_text(
            mapping.platform, mapping.program, mapping.language
        )
        if not self.templates.golden_exists(
            mapping.platform, mapping.program, mapping.language
        ):
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode=mapping.sync_mode,
                status="missing_source",
                historical_text=None,
                rendered_text=None,
                error="golden absent",
                blocking=False,
            )

        historical = self.templates.load_golden(
            mapping.platform, mapping.program, mapping.language
        )

        try:
            offer = self.renderer.offers.get_by_slug(mapping.program)
        except KeyError:
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode=mapping.sync_mode,
                status="missing_offer",
                historical_text=historical,
                rendered_text=None,
                error=f"programme {mapping.program!r} absent de offers.json",
                blocking=False,
            )

        variables = self.renderer.build_variables(mapping, offer=offer)

        # Champs mutables sans valeur offers → fallback valeur historique plateforme
        overrides: dict[str, str | None] = {}
        hist_values = dict(mapping.platform_values or {})
        extracted = extract_values_via_template(
            template, historical, mapping.mutable_fields, mapping.markers
        )
        for k, v in extracted.items():
            hist_values.setdefault(k, v)

        render_mutable = []
        for field_name in mapping.mutable_fields:
            val = variables.get(field_name)
            if val is None or val == "" or val == "None":
                # fallback to historical value so structure stays valid
                if field_name in hist_values:
                    overrides[field_name] = hist_values[field_name]
                else:
                    return DryRunResult(
                        platform=mapping.platform,
                        program=mapping.program,
                        language=mapping.language,
                        sync_mode=mapping.sync_mode,
                        status="render_error",
                        historical_text=historical,
                        rendered_text=None,
                        variables=variables,
                        error=f"Valeur manquante pour champ mutable {field_name}",
                        blocking=False,
                    )
            render_mutable.append(field_name)

        try:
            rendered = self.renderer.render(
                template, mapping, offer=offer, overrides=overrides or None
            )
        except ValueError as exc:
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode=mapping.sync_mode,
                status="render_error",
                historical_text=historical,
                rendered_text=None,
                variables=variables,
                error=str(exc),
                blocking=False,
            )

        changed_fields: dict[str, dict[str, str | None]] = {}
        for field_name in mapping.mutable_fields:
            old = hist_values.get(field_name)
            new = variables.get(field_name)
            if new is None or new == "":
                new = overrides.get(field_name)
            if old is not None and new is not None and old != new:
                changed_fields[field_name] = {"old": old, "new": new}

        golden_match = rendered == historical
        if golden_match:
            status = "in_sync"
        elif changed_fields:
            status = "pending_update"
        else:
            # Structure/text differs but no field-level diff extracted
            status = "pending_update"
            if not changed_fields:
                changed_fields["_text"] = {
                    "old": "(texte historique)",
                    "new": "(rendu offers.json different)",
                }

        sync_mode = mapping.sync_mode
        if self.capability == "MANUAL":
            sync_mode = "manual_review_required"

        return DryRunResult(
            platform=mapping.platform,
            program=mapping.program,
            language=mapping.language,
            sync_mode=sync_mode,
            status=status,
            historical_text=historical,
            rendered_text=rendered,
            variables=variables,
            changed_fields=changed_fields,
            golden_match=golden_match,
            error=(
                "publication MANUAL (prefer official import)"
                if self.capability == "MANUAL"
                else None
            ),
            blocking=False,
        )


class ManualPlatformAdapter(TextSyncAdapter):
    capability = "MANUAL"
