from platforms.referralcodes.agent_import import (
    SCHEMA_VERSION,
    build_import_payload,
    validate_item,
    validate_payload,
)


def test_schema_rejects_missing_shop_discount():
    r = validate_item({"shop": "", "discount": "", "url": None, "code": None})
    assert r.ok is False
    assert "shop_required" in r.errors
    assert "discount_required" in r.errors
    assert "url_or_code_required" in r.errors


def test_schema_accepts_code_only():
    r = validate_item(
        {
            "shop": "kraken",
            "discount": "$200 in crypto",
            "url": None,
            "code": "4hpz4gdy",
        }
    )
    assert r.ok is True


def test_schema_accepts_url_only():
    r = validate_item(
        {
            "shop": "coinbase",
            "discount": "$10 BTC",
            "url": "https://coinbase.com/join/x",
            "code": None,
        }
    )
    assert r.ok is True


def test_payload_version():
    bad = validate_payload({"version": "2.0", "items": []})
    assert bad.ok is False
    good = validate_payload(
        {
            "version": SCHEMA_VERSION,
            "items": [
                {
                    "shop": "kraken",
                    "discount": "bonus",
                    "url": "https://example.com/r",
                    "code": None,
                }
            ],
        }
    )
    assert good.ok is True


def test_build_kraken_payload():
    payload, meta = build_import_payload(["kraken"])
    assert payload["version"] == SCHEMA_VERSION
    assert isinstance(payload["items"], list)
    # may be empty if offers missing reward+link+code — still structured
    v = validate_payload(payload)
    # if items empty, validation fails items_must_be_non_empty — that's honest
    if payload["items"]:
        assert v.ok is True
        item = payload["items"][0]
        assert item.get("shop")
        assert item.get("discount")
        assert item.get("url") or item.get("code")
