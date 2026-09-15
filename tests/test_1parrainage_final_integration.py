from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import tools.run_verified_writers as dispatch


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "oneparrainage-edit-index.json"
MAPPINGS = ROOT / "data" / "platform-mappings"
TEMPLATES = ROOT / "data" / "platform-templates" / "1parrainage"


def test_authenticated_1parrainage_index_covers_all_current_editors():
    data = json.loads(INDEX.read_text(encoding="utf-8"))

    assert data["source"] == "authenticated_read_only_inventory"
    assert data["platform_writes"] == 0
    assert data["program_count"] == 39
    assert data["public_occurrences_total"] == 40
    assert data["edit_urls_found"] == 42
    assert data["unresolved_public_programs"] == []
    assert data["ambiguous_editors"] == []

    programs = data["programs"]
    assert len(programs) == 39
    unique_edit_urls = {
        url
        for item in programs.values()
        for url in (item.get("edit_urls") or [])
    }
    assert len(unique_edit_urls) == 42

    for slug, item in programs.items():
        policy = item.get("policy") or {}
        assert policy.get("mapping_exists") is True, slug
        assert policy.get("template_exists") is True, slug
        assert policy.get("live_replace_ready") is True, slug
        assert (MAPPINGS / f"1parrainage.{slug}.fr.json").exists(), slug
        assert (TEMPLATES / f"{slug}.fr.txt").exists(), slug
        assert item.get("edit_urls"), slug


def test_confirmed_field_scope_uses_live_body_authority_for_1parrainage():
    plan = SimpleNamespace(
        platform="1parrainage",
        program="demo",
        language="fr",
        live_validation_required=True,
        variables={
            "personal_code": "NEW-CODE",
            "personal_link": "NEW-LINK",
            "referee_reward": "50 €",
        },
        platform_values={
            "personal_code": "OLD-CODE",
            "personal_link": "OLD-LINK",
            "referee_reward": "20 €",
        },
        changed_fields={
            "personal_code": {"old": "OLD-CODE", "new": "NEW-CODE"},
            "personal_link": {"old": "OLD-LINK", "new": "NEW-LINK"},
            "referee_reward": {"old": "20 €", "new": "50 €"},
        },
        rendered="STATIC TEMPLATE MUST NOT BECOME LIVE BODY",
        historical="STATIC GOLDEN",
        structure_preserved=True,
    )

    scoped, note = dispatch._scope_plan_to_field(plan, "referee_reward")

    assert note is None
    assert scoped.changed_fields == {
        "referee_reward": {"old": "20 €", "new": "50 €"}
    }
    assert scoped.variables["referee_reward"] == "50 €"
    assert scoped.variables["personal_code"] == "OLD-CODE"
    assert scoped.variables["personal_link"] == "OLD-LINK"
    assert scoped.rendered == "STATIC TEMPLATE MUST NOT BECOME LIVE BODY"
    assert scoped.structure_preserved is True


def test_1parrainage_verified_baseline_never_overwrites_static_golden(monkeypatch, tmp_path):
    golden = tmp_path / "oneparrainage.demo.fr.golden.txt"
    golden.write_text("STATIC-GOLDEN-MUST-STAY", encoding="utf-8")
    mapping = tmp_path / "oneparrainage.demo.fr.json"
    mapping.write_text(
        json.dumps(
            {
                "platform_values": {
                    "personal_code": "OLD",
                    "referee_reward": "20 €",
                }
            }
        ),
        encoding="utf-8",
    )

    plan = SimpleNamespace(
        platform="1parrainage",
        program="demo",
        language="fr",
        rendered="NOT-THE-LIVE-EDITOR-BODY",
        mutable_fields=["personal_code", "referee_reward"],
        variables={"personal_code": "OLD", "referee_reward": "50 €"},
        edit_urls=["https://example.test/edit/1", "https://example.test/edit/2"],
        public_offer_ids=["1", "2"],
    )
    result = SimpleNamespace(edit_url="https://example.test/edit/1")

    monkeypatch.setattr(dispatch, "golden_path", lambda *_a, **_k: golden)
    monkeypatch.setattr(dispatch, "mapping_path", lambda *_a, **_k: mapping)
    monkeypatch.setattr(dispatch, "ROOT", tmp_path)

    out = dispatch._persist_verified_baseline(plan, result)

    assert golden.read_text(encoding="utf-8") == "STATIC-GOLDEN-MUST-STAY"
    saved = json.loads(mapping.read_text(encoding="utf-8"))
    assert saved["platform_values"]["referee_reward"] == "50 €"
    assert saved["write_status"] == "WRITE_VERIFIED"
    assert saved["edit_url"] == "https://example.test/edit/1"
    assert saved["edit_urls"] == plan.edit_urls
    assert saved["public_offer_ids"] == plan.public_offer_ids
    assert out["golden_written"] is False
