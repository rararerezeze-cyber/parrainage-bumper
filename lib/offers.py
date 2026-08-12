from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lib.paths import OFFERS_PATH

DEFAULT_BONUS_PARRAIN_OFFERS = Path(
    r"c:\Users\adrie\OneDrive\Documents\Projets\Sites web\Mon site de parrainage\data\offers.json"
)


class OffersRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or OFFERS_PATH

    def resolved_path(self) -> Path:
        # Preview/Telegram: env prioritaire si le fichier existe
        env_path = os.environ.get("BONUS_PARRAIN_OFFERS_PATH")
        if env_path:
            candidate = Path(env_path)
            if candidate.exists():
                return candidate
        if self.path.exists():
            return self.path
        if DEFAULT_BONUS_PARRAIN_OFFERS.exists():
            return DEFAULT_BONUS_PARRAIN_OFFERS
        raise FileNotFoundError(
            f"Aucune source offers.json trouvee (attendu: {self.path}). "
            "Executez: python tools/import_offers.py"
        )

    def load_all(self) -> list[dict[str, Any]]:
        with self.resolved_path().open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"{self.resolved_path()} doit contenir une liste d'offres")
        return data

    def get_by_slug(self, slug: str) -> dict[str, Any]:
        for offer in self.load_all():
            if offer.get("lk") == slug:
                return offer
        raise KeyError(f"Programme introuvable dans offers.json: {slug!r}")

    def resolve_field(self, offer: dict[str, Any], offer_field: str) -> str | None:
        value = offer.get(offer_field)
        if value is None:
            return None
        return str(value)
