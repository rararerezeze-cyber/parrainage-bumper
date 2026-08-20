from __future__ import annotations

from pathlib import Path

from tools import diagnose_1parrainage_final_gaps as diag


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "diagnose_1parrainage_final_gaps.py"
WORKFLOW = ROOT / ".github" / "workflows" / "diagnose_1parrainage_final_gaps.yml"


def test_public_full_view_is_derived_from_exact_read_more_chain():
    list_html = """
      <p onclick="pr_open_window('/parrain_definit.php?id_par=98906&amp;id=100408', 'x', 'y')">
        visible prefix ... <a>Lire la suite</a>
      </p>
    """
    bridge = diag._extract_bridge_url(
        list_html,
        "https://www.1parrainage.com/listeannonces_98906_Adrien89.php",
        "100408",
    )
    assert bridge == (
        "https://www.1parrainage.com/parrain_definit.php?id_par=98906&id=100408"
    )

    bridge_html = (
        '<iframe id="offreDetail" '
        'src="/detail_parrain.php?par=98906&amp;offre=100408"></iframe>'
    )
    assert diag._extract_detail_url(bridge_html, bridge) == (
        "https://www.1parrainage.com/detail_parrain.php?par=98906&offre=100408"
    )


def test_public_detail_extracts_complete_desc_detail_not_list_excerpt():
    html = """
      <div id="other">noise</div>
      <div class="col" id="desc_detail">
        <p>prefix cpbrgddy</p>
        <p>https://invite.kraken.com/JDNW/s5qudqe4</p>
        <p>terminal-marker</p>
      </div>
      <div>after</div>
    """
    block = diag._extract_detail_block(html)
    assert "terminal-marker" in block
    assert "after" not in block


def test_structure_and_diff_pin_exact_normalization():
    before = "<p>x</p>\r\n"
    after = "<p>x</p>\r\n\n"
    structure = diag._structure(after)
    diff = diag._first_diff(before, after)
    assert structure["len"] == len(after)
    assert structure["crlf_count"] == 1
    assert structure["bare_lf_count"] == 1
    assert structure["trailing_codepoints"] == ["U+000D", "U+000A", "U+000A"]
    assert diff == {
        "index": len(before),
        "before_codepoint": None,
        "after_codepoint": "U+000A",
        "before_remaining": 0,
        "after_remaining": 1,
    }


def test_diagnostic_is_read_only_and_artifact_excludes_raw_account_body():
    source = SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for forbidden in (
        "_click_save_once",
        "_fill_and_save",
        "execute_write",
        "guard_live_evidence_probe",
        "save_write_status",
    ):
        assert forbidden not in source
    assert "setData(value)" in source
    assert '"platform_writes": 0' in source
    assert '"body": current' not in source
    assert 'AUTOFRESH_LIVE_WRITES: "0"' in workflow
    assert "canary_write_1parrainage.py" not in workflow
    assert "git commit" not in workflow
    assert "git push" not in workflow
    assert "contents: read" in workflow
