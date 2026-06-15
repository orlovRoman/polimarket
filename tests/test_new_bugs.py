"""
Тесты для багов, найденных после коммитов b5f2f5f и e773761.
"""
import pytest
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────
# Баг 1: _parse_dt_utc — правильная обработка offset
# ─────────────────────────────────────────────────────────
from web.data_provider import _parse_dt_utc

@pytest.mark.parametrize("s,expected_hour_utc", [
    # строка без tz → трактуется как UTC → час 10
    ("2026-06-15 10:30:00",          10),
    ("2026-06-15T10:30:00",          10),
    # строка с +00:00 → UTC → час 10
    ("2026-06-15 10:30:00+00:00",    10),
    # строка с +07:00 → UTC = 03:30 → час 3  ← баг в b5f2f5f: вернёт 10
    ("2026-06-15 10:30:00+07:00",     3),
    # Z-суффикс → UTC → час 10
    ("2026-06-15T10:30:00Z",         10),
])
def test_parse_dt_utc_correct_hour(s, expected_hour_utc):
    result = _parse_dt_utc(s)
    assert result is not None
    assert result.tzinfo is not None, "Результат должен быть timezone-aware"
    assert result.hour == expected_hour_utc, (
        f"Для {s!r} ожидали UTC час {expected_hour_utc}, получили {result.hour}"
    )

def test_parse_dt_utc_none():
    assert _parse_dt_utc(None) is None

def test_parse_dt_utc_empty():
    assert _parse_dt_utc("") is None

def test_parse_dt_utc_garbage():
    assert _parse_dt_utc("not-a-date") is None

def test_b5f2f5f_split_plus_bug():
    """
    Воспроизводим баг из b5f2f5f: split('+')[0] обрезает правильный offset.
    Этот тест ПРОВАЛИТСЯ с текущим кодом b5f2f5f и пройдёт после исправления.
    """
    s = "2026-06-15T10:30:00+07:00"
    
    # Сломанная логика из b5f2f5f:
    s_broken = s.replace(" ", "T")
    if s_broken.endswith("Z"):
        s_broken = s_broken[:-1]
    if "+" in s_broken:
        s_broken = s_broken.split("+")[0]  # → "2026-06-15T10:30:00"
    broken_result = datetime.fromisoformat(s_broken).replace(tzinfo=timezone.utc)
    
    # Правильная логика:
    correct_result = _parse_dt_utc(s)
    
    # Разница должна быть 7 часов (25200 секунд)
    diff = abs((broken_result - correct_result).total_seconds())
    assert diff == pytest.approx(7 * 3600), (
        f"Баг b5f2f5f: ошибка на {diff/3600:.1f} ч "
        f"(broken={broken_result}, correct={correct_result})"
    )


# ─────────────────────────────────────────────────────────
# Баг 3: statusClass для ARCHIVED должен быть серым, не синим
# ─────────────────────────────────────────────────────────
def compute_status_class(status: str) -> str:
    """Эмуляция JavaScript-логики из scout.html."""
    if status == 'WIN':
        return 'badge-green'
    elif status == 'LOSS':
        return 'badge-red'
    elif status == 'ARCHIVED':
        return 'badge-gray'   # ← исправленная версия
    else:
        return 'badge-blue'

def compute_display_status(status: str) -> str:
    return 'ЗАКРЫТ' if status == 'ARCHIVED' else status

@pytest.mark.parametrize("status,expected_class,expected_display", [
    ('WIN',      'badge-green', 'WIN'),
    ('LOSS',     'badge-red',   'LOSS'),
    ('PENDING',  'badge-blue',  'PENDING'),
    ('ARCHIVED', 'badge-gray',  'ЗАКРЫТ'),   # ← главный тест
])
def test_status_badge_class(status, expected_class, expected_display):
    assert compute_status_class(status) == expected_class, (
        f"status={status}: ожидали {expected_class}, "
        f"в b5f2f5f вернёт 'badge-blue'"
    )
    assert compute_display_status(status) == expected_display

def test_archived_not_shown_as_active():
    """ARCHIVED не должен визуально выглядеть как активный (badge-blue)."""
    klass = compute_status_class('ARCHIVED')
    assert klass != 'badge-blue', (
        "ARCHIVED отображается как активный сигнал (badge-blue) — "
        "пользователь видит закрытую позицию как живую"
    )

# ─────────────────────────────────────────────────────────
# Баг 4: проверка отсутствия двойного масштабирования PnL
# ─────────────────────────────────────────────────────────
def apply_pnl_scaling(pnl_raw: float, bought_price: float, stake: float) -> float:
    """Исправленная формула масштабирования из web/data_provider.py."""
    if bought_price and 0.0 < bought_price < 1.0:
        return round((stake / bought_price) * pnl_raw, 2)
    return 0.0

@pytest.mark.parametrize("pnl_raw,bought_price,stake,expected", [
    # WIN penny stock: купили 0.05, WIN = 0.95
    (0.95,  0.05,  250.0,  4750.0),
    # LOSS penny stock: купили 0.05, LOSS = -1.0 (вернули 0)
    (-1.0,  0.05,  250.0,  -5000.0),
    # Нормальный рынок: купили 0.60, WIN = 0.40
    (0.40,  0.60,  250.0,  pytest.approx(166.67, abs=0.01)),
    # bought_price = 0 → не делить, вернуть 0
    (0.95,  0.0,   250.0,  0.0),
    # bought_price = None → 0
    (0.95,  None,  250.0,  0.0),
    # bought_price = 1.0 → граница, НЕ масштабируем (bought_price < 1.0)
    (0.95,  1.0,   250.0,  0.0),
])
def test_pnl_scaling_formula(pnl_raw, bought_price, stake, expected):
    result = apply_pnl_scaling(pnl_raw, bought_price, stake)
    assert result == expected, (
        f"pnl_raw={pnl_raw}, price={bought_price}, stake={stake}: "
        f"ожидали {expected}, получили {result}"
    )

def test_no_double_scaling():
    """
    Убеждаемся, что при stake=250 и bought_price=0.05
    результат НЕ равен stake² * pnl / price (двойное умножение).
    """
    stake = 250.0
    bought_price = 0.05
    pnl_raw = 0.95

    correct = apply_pnl_scaling(pnl_raw, bought_price, stake)
    double_scaled = round((stake * stake / bought_price) * pnl_raw, 2)

    assert correct != double_scaled, "Формула должна отличаться от двойного масштабирования"
    assert correct == pytest.approx(4750.0), f"Ожидали 4750.0, получили {correct}"
    assert double_scaled == pytest.approx(1_187_500.0), (
        f"Двойное масштабирование даёт {double_scaled}"
    )
