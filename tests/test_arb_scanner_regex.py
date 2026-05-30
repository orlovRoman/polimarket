from core.arb_scanner import _strip_price_tag, _PRICE_TAG_RE

def test_price_tag_re_strips_uppercase():
    title = "Will Fed raise rates? (YES: 72¢ | NO: 28¢)"
    result = _strip_price_tag(title)
    assert "YES" not in result
    assert "72¢" not in result
    assert "Will Fed raise rates?" in result

def test_price_tag_re_strips_lowercase_with_ignorecase():
    """После добавления re.IGNORECASE — lowercase тег тоже стрипается."""
    title = "Some market (yes: 45¢ | no: 55¢)"
    result = _strip_price_tag(title)
    assert "yes:" not in result.lower() or "45¢" not in result

def test_price_tag_re_no_false_strip():
    """Скобки без ценового тега не удаляются."""
    title = "Will GDP grow (IMF forecast)"
    result = _strip_price_tag(title)
    assert result == title, f"Лишнее удаление: '{result}'"
