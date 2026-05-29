import pytest
from core.models import Market, SwingSignal, AgentOpinion, Signal
from datetime import datetime, timezone


def _base_market(**kwargs):
    defaults = dict(
        id="mkt-1", platform="polymarket", title="Test",
        url="https://poly.com/t", outcome="YES",
        price=0.65,
        close_time=datetime(2026, 12, 1, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return Market(**defaults)


def _base_swing(**kwargs):
    defaults = dict(
        id="sw-1", market_id="mkt-1", platform="polymarket",
        hype_potential=0.7, recommendation="buy",
        target_outcome="YES", target_exit_price=0.75,
        confidence=0.8, reasoning="hype",
    )
    defaults.update(kwargs)
    return SwingSignal(**defaults)


def _base_opinion(**kwargs):
    defaults = dict(
        agent_name="SHADOW", market_id="mkt-1",
        opinion="ok", confidence=0.85, agree=True,
    )
    defaults.update(kwargs)
    return AgentOpinion(**defaults)


# ── Баг #1: SwingSignal.target_outcome нормализация ──────────

@pytest.mark.parametrize("raw,expected", [
    ("YES",     "YES"),
    ("yes",     "YES"),
    ("Yes",     "YES"),
    ("NO",      "NO"),
    ("no",      "NO"),
    ("No",      "NO"),
    ("Y",       "YES"),
    ("1",       "YES"),
    ("TRUE",    "YES"),
    ("BUY_YES", "YES"),
    ("N",       "NO"),
    ("0",       "NO"),
    ("FALSE",   "NO"),
    ("BUY_NO",  "NO"),
])
def test_swing_target_outcome_normalizes(raw, expected):
    sw = _base_swing(target_outcome=raw)
    assert sw.target_outcome == expected, \
        f"'{raw}' → ожидали '{expected}', получили '{sw.target_outcome}'"


def test_swing_target_outcome_unknown_preserved():
    """Неизвестное значение не падает — сохраняется для диагностики"""
    sw = _base_swing(target_outcome="MAYBE")
    assert sw.target_outcome == "MAYBE"


def test_swing_target_outcome_trailing_space_stripped():
    """Пробелы trimmed"""
    sw = _base_swing(target_outcome="  YES  ")
    assert sw.target_outcome == "YES"


# ── Баг #2: SwingSignal.confidence и hype_potential clamp ────

@pytest.mark.parametrize("field,value,expected", [
    ("confidence",    1.5,    1.0),
    ("confidence",    0.0,    0.0),
    ("confidence",    0.85,   0.85),
    ("confidence",    95.0,   0.95),   # LLM вернул проценты
    ("hype_potential", 1.5,   1.0),
    ("hype_potential", -0.3,  0.0),
    ("hype_potential", 75.0,  0.75),   # LLM вернул проценты
])
def test_swing_clamp_0_1(field, value, expected):
    sw = _base_swing(**{field: value})
    assert getattr(sw, field) == pytest.approx(expected), \
        f"{field}={value} → ожидали {expected}, получили {getattr(sw, field)}"


def test_swing_confidence_none_not_accepted():
    """confidence — обязательное поле, None не принимается"""
    with pytest.raises(Exception):
        _base_swing(confidence=None)


# ── Баг #3: Market.price валидация ───────────────────────────

def test_market_price_in_cents_auto_converted():
    """Цена 65.0 (центы) → 0.65 (доли)"""
    m = _base_market(price=65.0)
    assert m.price == pytest.approx(0.65)


def test_market_price_in_fractions_unchanged():
    """Цена 0.65 остаётся 0.65"""
    m = _base_market(price=0.65)
    assert m.price == pytest.approx(0.65)


def test_market_price_zero_accepted():
    m = _base_market(price=0.0)
    assert m.price == pytest.approx(0.0)


def test_market_price_one_accepted():
    m = _base_market(price=1.0)
    assert m.price == pytest.approx(1.0)


def test_market_price_100_cents_converted():
    """100 центов → 1.0"""
    m = _base_market(price=100.0)
    assert m.price == pytest.approx(1.0)


def test_market_price_clamped_below_zero():
    """Отрицательная цена → 0.0"""
    m = _base_market(price=-5.0)
    assert m.price == pytest.approx(0.0)


def test_market_price_clamped_above_100():
    """Цена > 100 → 1.0 (после конвертации)"""
    m = _base_market(price=150.0)
    assert m.price == pytest.approx(1.0)


# ── Баг #4: AgentOpinion.confidence авто-конвертация ─────────

def test_agent_opinion_confidence_percent_converted():
    """confidence=95.0 → 0.95"""
    op = _base_opinion(confidence=95.0)
    assert op.confidence == pytest.approx(0.95)


def test_agent_opinion_confidence_fraction_unchanged():
    """confidence=0.85 → 0.85"""
    op = _base_opinion(confidence=0.85)
    assert op.confidence == pytest.approx(0.85)


def test_agent_opinion_confidence_above_100_clamped():
    """confidence=150.0 → 1.0"""
    op = _base_opinion(confidence=150.0)
    assert op.confidence == pytest.approx(1.0)


def test_agent_opinion_confidence_clamped_below_zero():
    """confidence=-0.1 → 0.0"""
    op = _base_opinion(confidence=-0.1)
    assert op.confidence == pytest.approx(0.0)


# ── Интеграционный: адаптер возвращает цену в центах ─────────

def test_prefilter_with_cent_price_handled_correctly():
    """Market с ценой 65 центов корректно проходит prefilter"""
    from core.workflow import _prefilter_markets
    from unittest.mock import patch

    market_dict = {
        "price":      65.0,   # центы
        "volume":     10000,
        "close_time": "2026-12-01T00:00:00+00:00",
    }

    with patch("config.PRICE_RANGE_MIN", 0.1), \
         patch("config.PRICE_RANGE_MAX", 0.9):
        # Market модель конвертирует 65 → 0.65 до prefilter
        # Проверяем что логика не ломается
        m = _base_market(price=65.0)
        assert m.price == pytest.approx(0.65)
        # 0.65 попадает в диапазон [0.1, 0.9] ✓


def test_full_chain_swing_buy_yes_upper_case():
    """LLM вернул recommendation='BUY', target_outcome='yes' → корректный консенсус"""
    from core.workflow import make_consensus
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.market.id = "mkt-1"

    shadow = _base_opinion(agree=True, liquidity_risk="low")
    swing  = _base_swing(recommendation="BUY", target_outcome="yes")

    assert swing.recommendation == "buy"
    assert swing.target_outcome == "YES"
    assert shadow.liquidity_risk == "LOW"

    decision = make_consensus(ctx, signal=None, swing_signal=swing, opinion_shadow=shadow)
    assert decision.status == "saved"
