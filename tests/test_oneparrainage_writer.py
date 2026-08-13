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
