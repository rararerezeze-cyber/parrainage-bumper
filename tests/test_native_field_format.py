from lib.native_field_format import adapt_monitor_value_to_native


def test_simple_amount_keeps_native_spacing():
    assert (
        adapt_monitor_value_to_native("referee_reward", "jusqu'à 160 €", "200 €")
        == "160 €"
    )
    assert adapt_monitor_value_to_native("min_deposit", "300 €", "300€") == "300€"


def test_simple_amount_inside_native_phrase():
    native = "5€ de bienvenue crédités dès votre inscription via mon code parrain ✅"
    assert (
        adapt_monitor_value_to_native("referee_reward", "3 €", native)
        == "3€ de bienvenue crédités dès votre inscription via mon code parrain ✅"
    )


def test_non_amount_does_not_overwrite_native_span():
    assert (
        adapt_monitor_value_to_native(
            "referee_reward", "Programme à confirmer", "200 €"
        )
        == "200 €"
    )


def test_structured_phrase_passes_through():
    incoming = "10 € bonus + 10 € freebets"
    native = "50 € à l’inscription + premier pari remboursé jusqu’à 100 en CASH € ✅"
    assert adapt_monitor_value_to_native("referee_reward", incoming, native) == incoming


def test_qualification_days_number_only():
    assert adapt_monitor_value_to_native("qualification_days", "30", "30") == "30"
    assert adapt_monitor_value_to_native("qualification_days", "15", "30") == "15"


def test_reward_type_untouched():
    assert (
        adapt_monitor_value_to_native("reward_type", "tiered_cash", None) == "tiered_cash"
    )
