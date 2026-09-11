from bumper import _classify_slider_state


def test_slider_state_accepts_legacy_true_flag():
    assert _classify_slider_state("true", widget_visible=True) == "success"


def test_slider_state_accepts_numeric_success_flag():
    assert _classify_slider_state("1", widget_visible=True) == "success"


def test_slider_state_accepts_widget_disappearance():
    assert _classify_slider_state(None, widget_visible=False) == "success"


def test_slider_state_rejects_explicit_false_flag():
    assert _classify_slider_state("false", widget_visible=True) == "failure"


def test_slider_state_rejects_failure_text():
    assert (
        _classify_slider_state(
            None,
            widget_visible=True,
            root_text="Échec, veuillez réessayer",
        )
        == "failure"
    )


def test_slider_state_does_not_guess_unknown_dom_as_failure():
    assert (
        _classify_slider_state(
            None,
            widget_visible=True,
            root_text="Vérification de sécurité requise",
        )
        == "indeterminate"
    )


def test_slider_state_explicit_success_marker_wins():
    assert (
        _classify_slider_state(
            None,
            widget_visible=True,
            success_marker=True,
        )
        == "success"
    )
