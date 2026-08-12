from __future__ import annotations

from lib.models import DryRunResult, PlatformMapping
from lib.offers import OffersRepository
from lib.renderer import MappingRepository, Renderer, TemplateRepository
from lib.sync_state import SyncStateStore
from platforms.base import PlatformAdapter


class SuperParrainAdapter(PlatformAdapter):
    platform_id = "super-parrain"

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
        if mapping.template_status == "missing_source" or not self.templates.exists(
            mapping.platform, mapping.program, mapping.language
        ):
            error = (
                "missing_source: template absent pour "
                f"{mapping.platform}/{mapping.program}.{mapping.language}"
            )
            self.sync_state.upsert_entry(
                mapping.platform,
                mapping.program,
                mapping.language,
                {
                    "status": "missing_source",
                    "last_error": error,
                    "last_attempt_at": None,
                },
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
                blocking=True,
            )

        template = self.templates.load_text(mapping.platform, mapping.program, mapping.language)
        if not self.templates.golden_exists(mapping.platform, mapping.program, mapping.language):
            error = (
                "missing_source: texte historique (golden) absent pour "
                f"{mapping.platform}/{mapping.program}.{mapping.language}"
            )
            self.sync_state.upsert_entry(
                mapping.platform,
                mapping.program,
                mapping.language,
                {"status": "missing_source", "last_error": error},
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
                blocking=True,
            )

        historical = self.templates.load_golden(mapping.platform, mapping.program, mapping.language)
        offer = self.renderer.offers.get_by_slug(mapping.program)
        variables = self.renderer.build_variables(mapping, offer=offer)

        try:
            rendered = self.renderer.render(template, mapping, offer=offer)
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
                blocking=True,
            )

        golden_match = rendered == historical
        if not golden_match:
            return DryRunResult(
                platform=mapping.platform,
                program=mapping.program,
                language=mapping.language,
                sync_mode=mapping.sync_mode,
                status="golden_mismatch",
                historical_text=historical,
                rendered_text=rendered,
                variables=variables,
                golden_match=False,
                error=(
                    "Le rendu avec les valeurs actuelles de offers.json "
                    "ne reproduit pas le texte historique."
                ),
                blocking=True,
            )

        entry = self.sync_state.get_entry(mapping.platform, mapping.program, mapping.language) or {}
        last_values = entry.get("last_values") or {}
        changed_fields: dict[str, dict[str, str | None]] = {}
        for field_name in mapping.mutable_fields:
            current = variables.get(field_name)
            previous = last_values.get(field_name)
            if previous is not None and previous != current:
                changed_fields[field_name] = {"old": previous, "new": current}

        return DryRunResult(
            platform=mapping.platform,
            program=mapping.program,
            language=mapping.language,
            sync_mode=mapping.sync_mode,
            status="ready",
            historical_text=historical,
            rendered_text=rendered,
            variables=variables,
            changed_fields=changed_fields,
            golden_match=True,
            blocking=False,
        )
