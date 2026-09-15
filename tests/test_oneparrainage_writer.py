"""1Parrainage PC-off writer uses proven CKEditor + scoped Envoyer."""
from __future__ import annotations

from pathlib import Path

from platforms.oneparrainage import writer as w


def test_proven_selectors():
    assert w.CK_ID == "edit_parrainage_presentation"
    assert "parrainages/edit" in w.EDIT_FORM


def test_fill_and_save_source_is_ckeditor_not_hidden_textarea():
    src = Path(w.__file__).read_text(encoding="utf-8")
    assert "ckeditor.setData" in src
    assert "form[action*=\"parrainages/edit\"]" in src
    assert "texte_results" in src or "recherch" in src
    # Hidden CKEditor textarea.fill() is the GH timeout failure mode.
    assert "Never textarea.fill() on the hidden CKEditor field" in src



def test_live_replace_updates_only_historically_authorized_occurrences():
    source = (
        "<p>Prime 200 &euro;</p>"
        "<p>Total 200 &euro;</p>"
        "<p>Dépôt 200 &euro;</p>"
        "<p>Trade 200 &euro;</p>"
    )
    rendered, count = w._replace_live_field(source, "200 €", "250 €", 2)
    assert count == 2
    assert rendered.count("250 &euro;") == 2
    assert rendered.count("200 &euro;") == 2
    assert "Dépôt 200 &euro;" in rendered
    assert "Trade 200 &euro;" in rendered


def test_live_render_handles_entity_encoded_totalenergies_body():
    from types import SimpleNamespace

    source = (
        "<p>20&euro; OFFERTS</p>"
        "<p>https://redir-totalenergies.web.app/je-parraine?_bp=114052844</p>"
        "<p>114052844</p>"
        "<p>renseignez le code parrain 114052844</p>"
    )
    plan = SimpleNamespace(
        changed_fields={"referee_reward": {"old": "20€ OFFERTS", "new": "50€ OFFERTS"}},
        platform_values={"referee_reward": "20€ OFFERTS"},
        variables={"referee_reward": "50€ OFFERTS"},
        marker_counts={"referee_reward": 1},
    )
    rendered, details = w._build_live_rendered(source, plan)
    assert "50&euro; OFFERTS" in rendered
    assert "20&euro; OFFERTS" not in rendered
    assert "114052844" in rendered
    assert details["referee_reward"]["replaced_spans"] == 1


def test_live_render_fails_closed_when_expected_span_is_missing():
    from types import SimpleNamespace
    import pytest

    plan = SimpleNamespace(
        changed_fields={"referee_reward": {"old": "20 €", "new": "50 €"}},
        platform_values={"referee_reward": "20 €"},
        variables={"referee_reward": "50 €"},
        marker_counts={"referee_reward": 2},
    )
    with pytest.raises(RuntimeError, match="live_replace_span_mismatch"):
        w._build_live_rendered("<p>20 &euro;</p>", plan)
