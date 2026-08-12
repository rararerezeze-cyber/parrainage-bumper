from __future__ import annotations

from abc import ABC, abstractmethod

from lib.models import DryRunResult, PlatformMapping


class PlatformAdapter(ABC):
    platform_id: str

    @abstractmethod
    def dry_run(self, mapping: PlatformMapping) -> DryRunResult:
        raise NotImplementedError

    def update_content(self, mapping: PlatformMapping, rendered_text: str) -> None:
        raise NotImplementedError(
            f"{self.platform_id}: publication reelle non autorisee en Phase 2"
        )
