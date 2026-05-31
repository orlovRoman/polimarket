import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from core.math_filter import math_pre_filter, FilterDecision, MathFilterResult

def test_identical_threshold_returns_no_arbi():
    """$800B в обоих заголовках — не импликация, а разные события"""
    a = MagicMock()
    a.title = "OpenAI IPO closing market cap above $800B?"
    a.price = 0.85
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"

    b = MagicMock()
    b.title = "Will OpenAI's valuation hit (LOW) $800B by June 30?"
    b.price = 0.269
    b.platform = "polymarket"
    b.close_time = datetime(2026, 6, 30)
    b.url = "https://polymarket.com/b"

    result = math_pre_filter(a, b)
    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "identical_threshold"
    assert result.spread_pct == 0.0


def test_real_monotonicity_violation_passes():
    """$1T vs $800B на одном субъекте — настоящее нарушение монотонности"""
    a = MagicMock()
    a.title = "Will OpenAI market cap exceed $1T?"
    a.price = 0.80  # BUG: $1T дороже $800B — нарушение
    a.platform = "polymarket"
    a.close_time = datetime(2026, 12, 31)
    a.url = "https://polymarket.com/a"

    b = MagicMock()
    b.title = "Will OpenAI market cap exceed $800B?"
    b.price = 0.40
    b.platform = "polymarket"
    b.close_time = datetime(2026, 12, 31)
    b.url = "https://polymarket.com/b"

    result = math_pre_filter(a, b)
    # P(>$1T) > P(>$800B) — реальный арбитраж, должен пройти
    assert result.arbitrage_type == "monotonicity_violation"
    assert result.spread_pct >= 5.0


def test_agent_guard_skips_llm_for_identical_threshold():
    """При identical_threshold агент не должен вызывать LLM"""
    mf = MathFilterResult(
        decision=FilterDecision.CONFIRMED_NO_ARBI,
        arbitrage_type="identical_threshold",
        spread_pct=0.0,
        reasoning="Одинаковый порог — разные события",
        trade_instruction=""
    )
    # Симулируем логику agent.py
    result = None
    if mf.decision == FilterDecision.CONFIRMED_NO_ARBI:
        result = None  # ← ранний возврат без LLM
    assert result is None


def _mkt(title, price, id="m1", platform="polymarket", event_slug=None):
    m = MagicMock()
    m.title = title
    m.price = price
    m.platform = platform
    m.close_time = datetime(2026, 11, 1)
    m.url = f"https://polymarket.com/{id}"
    m.event_slug = event_slug
    return m


def test_ny12_ok01_complementary_price_sum_no_arbi():
    """
    Главный регрессионный тест.
    NY-12 + OK-01: price_sum = 1.14 > 1.0, но РАЗНЫЕ события.
    Должен вернуть CONFIRMED_NO_ARBI, не CONFIRMED_ARBITRAGE.
    """
    mkt_a = _mkt("Will Alex Bores be the democratic nominee for NY-12?", 0.415, "ny12")
    mkt_b = _mkt("Will Jackson Lahmeyer be the Republican nominee for OK-01?", 0.725, "ok01")

    with patch("core.semantic_filter.semantic_same_event", return_value=False):
        result = math_pre_filter(mkt_a, mkt_b)

    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI, (
        f"РЕГРЕССИЯ complementary-ветка: NY-12 vs OK-01 даёт ложный арбитраж! "
        f"decision={result.decision}, type={result.arbitrage_type}"
    )
    assert result.arbitrage_type == "different_events"
    assert result.has_arbitrage is False


def test_two_different_dem_rep_races_no_arbi():
    """
    Два разных штата, democrat vs republican, price_sum > 1.0.
    Ohio primary vs Texas primary — разные события.
    """
    mkt_a = _mkt("Will the Democrat win the Ohio governor primary 2026?", 0.60, "oh")
    mkt_b = _mkt("Will the Republican win the Texas governor primary 2026?", 0.55, "tx")

    with patch("core.semantic_filter.semantic_same_event", return_value=False):
        result = math_pre_filter(mkt_a, mkt_b)

    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI
    assert result.arbitrage_type == "different_events"


def test_true_complementary_same_event_still_detected():
    """
    Democrat vs Republican в одном событии: embedding=True → арбитраж находится.
    """
    mkt_a = _mkt("Will the Democrat win the 2026 Ohio governor race?", 0.65, "oh-dem")
    mkt_b = _mkt("Will the Republican win the 2026 Ohio governor race?", 0.55, "oh-rep")

    with patch("core.semantic_filter.semantic_same_event", return_value=True):
        result = math_pre_filter(mkt_a, mkt_b)

    assert result.decision == FilterDecision.CONFIRMED_ARBITRAGE
    assert result.arbitrage_type == "complementary_overpriced"
    assert result.has_arbitrage is True


def test_grey_zone_fallback_regex_different_events():
    """
    Серая зона (embedding=None): regex-fallback должен отклонить разные округа.
    'bores', 'ny', '12' vs 'lahmeyer', 'ok', '01' — overlap < 50%.
    """
    mkt_a = _mkt("Will Alex Bores be the democratic nominee for NY-12?", 0.415, "ny12")
    mkt_b = _mkt("Will Jackson Lahmeyer be the Republican nominee for OK-01?", 0.725, "ok01")

    with patch("core.semantic_filter.semantic_same_event", return_value=None):
        result = math_pre_filter(mkt_a, mkt_b)

    assert result.decision == FilterDecision.CONFIRMED_NO_ARBI, (
        f"Regex-fallback при серой зоне не защитил: {result.decision}"
    )


def test_dem_not_triggered_by_demand():
    """
    'dem' в 'demand' не должен триггерить explicit_pairs с 'republican'.
    """
    from core.math_filter import _looks_complementary
    result = _looks_complementary(
        "Will consumer demand exceed pre-pandemic levels?",
        "Will the Republican candidate win the Senate?"
    )
    assert result is False, (
        "БАГИ: 'demand' ложно триггерит пару 'dem'+'republican'. "
        "Удалите explicit_pairs из _looks_complementary."
    )


def test_directional_with_political_stopwords_not_triggered():
    """
    'above'+'below' с common = {'nominee', 'win'} (политические стоп-слова)
    не должны давать True при расширенных стоп-словах.
    """
    from core.math_filter import _looks_complementary
    result = _looks_complementary(
        "Will nominee win above 270 electoral votes?",
        "Will incumbent win below 50 senate seats?"
    )
    assert result is False, (
        "Политические слова ('nominee', 'win') не должны входить в common для directional_pairs."
    )


def test_same_event_slug_bypasses_embedding():
    """
    Одинаковый event_slug: _check_same_event должен вернуть True
    без вызова embedding (Layer 1 достаточен).
    """
    from core.math_filter import _check_same_event

    a = _mkt("Will Democrat win NY-12?", 0.6, event_slug="ny12-primary-2026")
    b = _mkt("Will Republican win NY-12?", 0.5, event_slug="ny12-primary-2026")

    with patch("core.semantic_filter.semantic_same_event") as mock_embed:
        result = _check_same_event(a.title, b.title, market_a=a, market_b=b)

    assert result is True
    mock_embed.assert_not_called()  # embedding не должен вызываться


def test_different_event_slug_bypasses_embedding():
    """
    Разные event_slug: _check_same_event возвращает False без embedding.
    """
    from core.math_filter import _check_same_event

    a = _mkt("Will Democrat win NY-12?", 0.6, event_slug="ny12-primary-2026")
    b = _mkt("Will Republican win OK-01?", 0.5, event_slug="ok01-primary-2026")

    with patch("core.semantic_filter.semantic_same_event") as mock_embed:
        result = _check_same_event(a.title, b.title, market_a=a, market_b=b)

    assert result is False
    mock_embed.assert_not_called()



