"""
tests/test_fixes_v2.py
Тесты для трёх проблем после коммита 30083c9.
"""
import pytest


# ═══════════════════════════════════════════════════════════
# Fix 1: _parse_threshold — finditer вместо search (все контекстные матчи)
# ═══════════════════════════════════════════════════════════

def test_parse_threshold_market_id_not_captured_as_threshold():
    """
    'Kalshi market 1205 have S&P 500 above 730'
    Pass 1 (finditer) должен поймать 730 после 'above'.
    Без фикса: если Pass 1 падал — Pass 2 max() вернул бы 1205 (ID рынка).
    """
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will Kalshi market 1205 have S&P 500 above 730?")
    assert result is not None
    assert result[0] == 730.0, (
        f"Ожидали 730 (после 'above'), получили {result[0]}. "
        f"Pass 2 max() ошибочно захватывает 1205 (ID рынка)."
    )


def test_parse_threshold_two_large_numbers_takes_contextual():
    """
    'SPX hits 6000 from 5800 base' — два числа, контекстное слово перед 6000.
    Pass 1 (finditer) должен взять 6000, а не 5800.
    """
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will SPX hits 6000 from 5800 base level?")
    assert result is not None
    assert result[0] == 6000.0, f"Ожидали 6000 (после 'hits'), получили {result[0]}"


def test_parse_threshold_pass1_wins_over_pass2_max():
    """
    'S&P 500 index above 5500, previous high was 5800'
    Pass 1 должен взять 5500 (после 'above'), а не 5800 (max всех чисел).
    """
    from core.math_filter import _parse_threshold
    result = _parse_threshold("S&P 500 index above 5500, previous high was 5800")
    assert result is not None
    assert result[0] == 5500.0, (
        f"Ожидали 5500 (Pass 1, после 'above'), получили {result[0]}. "
        f"Pass 2 max() ошибочно забирает 5800."
    )


def test_parse_threshold_first_contextual_match_wins():
    """
    Если есть два контекстных слова — берём первое.
    'above 5500, below 6000' → 5500 (первый матч 'above').
    """
    from core.math_filter import _parse_threshold
    result = _parse_threshold("S&P above 5500 but below 6000 for next week?")
    assert result is not None
    # Первый finditer матч — 'above 5500'
    assert result[0] == 5500.0, f"Ожидали 5500 (первый контекстный матч), получили {result[0]}"


def test_parse_threshold_skips_year_in_contextual_hit():
    """
    'hit 2027 futures price at 5800' — контекст 'hit' перед годом 2027.
    finditer должен пропустить год и взять 5800 из следующего контекстного слова 'at'.
    """
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will SPX hit 2027 futures price at 5800?")
    assert result is not None
    assert result[0] == 5800.0, (
        f"Ожидали 5800 (пропуск года 2027 в Pass 1, след. контекстный матч), получили {result[0]}"
    )


# ═══════════════════════════════════════════════════════════
# Fix 2: _SESSION_DEDUP_TTL_SEC >= SCREENING_INTERVAL_SEC
# (исправлен в итерации 6, проверяем что регрессии нет)
# ═══════════════════════════════════════════════════════════

def test_dedup_ttl_not_less_than_screening_interval():
    """
    _SESSION_DEDUP_TTL_SEC (1800) должен быть >= SCREENING_INTERVAL_SEC (1800).
    Иначе рынки анализируются повторно внутри одного цикла скрининга.
    """
    from config import SCREENING_INTERVAL_SEC
    from core.workflow import _SESSION_DEDUP_TTL_SEC

    assert _SESSION_DEDUP_TTL_SEC >= SCREENING_INTERVAL_SEC, (
        f"DEDUP TTL ({_SESSION_DEDUP_TTL_SEC}s) < SCREENING_INTERVAL ({SCREENING_INTERVAL_SEC}s). "
        f"Баг 2: рынки будут анализироваться повторно внутри одного скрининга."
    )


# ═══════════════════════════════════════════════════════════
# Fix 3: _looks_complementary без устаревших политических пар
# ═══════════════════════════════════════════════════════════

def test_looks_complementary_generic_parties_work():
    """Democrat vs Republican — общие термины работают."""
    from core.math_filter import _looks_complementary
    assert _looks_complementary(
        "Will a Democrat win the 2026 midterms?",
        "Will a Republican win the 2026 midterms?"
    ) is True


def test_looks_complementary_no_false_positive_trump_trump():
    """
    Два рынка про Трампа без directional pair — НЕ комплементарные.
    До фикса: explicit_pair ('trump', 'harris') мог дать false positive
    если один рынок упоминал 'trump' и 'harris' в одном заголовке.
    """
    from core.math_filter import _looks_complementary
    result = _looks_complementary(
        "Will Trump sign the tariff bill?",
        "Will Trump veto the defense bill?"
    )
    assert result is False, (
        f"Два рынка про Трампа без directional pair не должны быть комплементарными."
    )


def test_looks_complementary_directional_pairs_still_work():
    """Directional pairs (above/below, over/under) не сломаны."""
    from core.math_filter import _looks_complementary
    assert _looks_complementary(
        "Will S&P 500 close above 5500?",
        "Will S&P 500 close below 5500?"
    ) is True
    assert _looks_complementary(
        "Will BTC be over $100K?",
        "Will BTC be under $100K?"
    ) is True


# ═══════════════════════════════════════════════════════════
# REGRESSION: предыдущие фиксы не сломаны
# ═══════════════════════════════════════════════════════════

def test_parse_threshold_sp500_5500_regression():
    """Регрессия: оригинальный баг-кейс S&P 500 всё ещё работает."""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("S&P 500 closes above 5500")
    assert result is not None
    assert result[0] == 5500.0


def test_parse_threshold_btc_200k_regression():
    """Регрессия: BTC $200K не сломан."""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will BTC hit $200K?")
    assert result is not None
    assert result[0] == 200_000.0
    assert result[1] == "usd"


def test_parse_threshold_percentage_regression():
    """Регрессия: проценты не сломаны."""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will inflation exceed 4.5%?")
    assert result is not None
    assert result[0] == 4.5
    assert result[1] == "%"


def test_parse_threshold_year_not_threshold_regression():
    """Регрессия: год не интерпретируется как порог."""
    from core.math_filter import _parse_threshold
    result = _parse_threshold("Will X happen by 2026?")
    assert result is None
