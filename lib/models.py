from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SYNC_MODES = frozenset({"SAFE_AUTO", "REVIEW", "MANUAL", "manual_review_required"})
TEMPLATE_STATUSES = frozenset({"ready", "missing_source", "manual_review_required"})


@dataclass
class PlatformMapping:
    platform: str
    program: str
    language: str
    sync_mode: str
    mutable_fields: list[str]
    markers: dict[str, str]
    offer_fields: dict[str, str]
    template_status: str = "ready"
    announcement_url: str | None = None
    edit_url: str | None = None
    notes: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformMapping":
        return cls(
            platform=data["platform"],
            program=data["program"],
            language=data["language"],
            sync_mode=data.get("sync_mode", "REVIEW"),
            mutable_fields=list(data.get("mutable_fields", [])),
            markers=dict(data.get("markers", {})),
            offer_fields=dict(data.get("offer_fields", {})),
            template_status=data.get("template_status", "ready"),
            announcement_url=data.get("announcement_url"),
            edit_url=data.get("edit_url"),
            notes=data.get("notes"),
        )


@dataclass
class RenderResult:
    platform: str
    program: str
    language: str
    rendered: str
    variables: dict[str, str | None]
    mutable_fields: list[str]


@dataclass
class DryRunResult:
    platform: str
    program: str
    language: str
    sync_mode: str
    status: str
    historical_text: str | None
    rendered_text: str | None
    variables: dict[str, str | None] = field(default_factory=dict)
    changed_fields: dict[str, dict[str, str | None]] = field(default_factory=dict)
    golden_match: bool | None = None
    error: str | None = None
    blocking: bool = False

    def to_report_lines(self) -> list[str]:
        lines = [
            f"{self.program.title()}",
            f"Plateforme: {self.platform} ({self.language})",
            f"Mode: {self.sync_mode} | Statut: {self.status}",
        ]
        if self.error:
            lines.append(f"Erreur: {self.error}")
            return lines
        if self.golden_match is not None:
            lines.append(f"Golden match: {'oui' if self.golden_match else 'NON'}")
        for field_name, diff in self.changed_fields.items():
            old = diff.get("old")
            new = diff.get("new")
            lines.append(f"{field_name}: {old!r} -> {new!r}")
        if self.historical_text is not None and self.rendered_text is not None:
            if self.historical_text != self.rendered_text:
                lines.append("--- historique ---")
                lines.append(self.historical_text)
                lines.append("--- rendu ---")
                lines.append(self.rendered_text)
        return lines
