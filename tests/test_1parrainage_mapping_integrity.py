from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAPPINGS = ROOT / "data" / "platform-mappings"
TEMPLATES = ROOT / "data" / "platform-templates" / "1parrainage"


def test_all_1parrainage_mutable_mappings_have_real_template_markers():
    problems: list[str] = []
    paths = sorted(MAPPINGS.glob("1parrainage.*.fr.json"))
    assert paths, "no 1parrainage mappings found"

    for mapping_path in paths:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
        slug = data["program"]
        mutable = list(data.get("mutable_fields") or [])
        template_path = TEMPLATES / f"{slug}.fr.txt"
        golden_path = TEMPLATES / f"{slug}.fr.golden.txt"
        assert template_path.exists(), f"missing template: {template_path}"
        assert golden_path.exists(), f"missing golden: {golden_path}"

        template = template_path.read_text(encoding="utf-8")
        golden = golden_path.read_text(encoding="utf-8")
        markers = data.get("markers") or {}

        missing = [
            field for field in mutable
            if not markers.get(field) or markers[field] not in template
        ]
        if missing:
            problems.append(f"{slug}: mutable fields without template marker: {missing}")

        unresolved = [
            field for field in mutable
            if markers.get(field) and markers[field] in golden
        ]
        if unresolved:
            problems.append(f"{slug}: unresolved markers in golden: {unresolved}")

        if mutable and template == golden:
            problems.append(f"{slug}: template equals golden despite mutable_fields={mutable}")

        if slug != "kraken" and "leslogos/kraken.jpg" in template.lower():
            problems.append(f"{slug}: template contains Kraken logo")

    assert not problems, "\n".join(problems)
