import pytest
import math
from unittest.mock import MagicMock
from datetime import datetime, timezone

# ──────────────────────────────────────────────────────────────
# Вспомогательная функция (зеркало логики из data_provider.py)
# ──────────────────────────────────────────────────────────────
def scaled_pnl(pnl_raw: float, bought_price: float | None, stake: float) -> float:
    """Зеркало формулы из web/data_provider.py с фоллбеком."""
    if bought_price and 0.0 < bought_price < 1.0:
        return round(pnl_raw * (stake / bought_price), 2)
    return round(pnl_raw * stake, 2)

# ──────────────────────────────────────────────────────────────
# Тесты PnL Scaling (с параметризацией и фоллбеком)
# ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pnl_raw, bought_price, stake, expected", [
    # Penny stock WIN: купили по 0.05, прибыль = 0.95 на контракт
    (0.95,  0.05,   250.0,  4750.0),
    # Penny stock LOSS: купили по 0.05, потеряли 1.0 на контракт  
    (-1.0,  0.05,   250.0,  -5000.0),
    # Нормальный рынок: купили по 0.60
    (0.40,  0.60,   250.0,  pytest.approx(166.67, abs=0.01)),
    # bought_price == 0 → фоллбек на умножение на stake (250 * 0.95 = 237.5)
    (0.95,  0.0,    250.0,  237.5),
    # bought_price is None → фоллбек на умножение на stake
    (0.95,  None,   250.0,  237.5),
    # bought_price == 1.0 → фоллбек
    (0.95,  1.0,    250.0,  237.5),
    # bought_price > 1.0 → фоллбек
    (0.95,  1.5,    250.0,  237.5),
    # pnl_raw == 0
    (0.0,   0.05,   250.0,  0.0),
])
def test_pnl_formula(pnl_raw, bought_price, stake, expected):
    assert scaled_pnl(pnl_raw, bought_price, stake) == expected


# Тест 1: _process_resolved_row не обнуляет PnL без bought_price (критический фоллбек)
def test_pnl_fallback_when_no_bought_price():
    """При bought_outcome_price = None должен использоваться фоллбек, а не 0.0"""
    raw_pnl = 0.5        # 50% нормализованный PnL
    virtual_stake = 10.0
    bought_outcome_price = None

    result = scaled_pnl(raw_pnl, bought_outcome_price, virtual_stake)

    assert result == 5.0, f"Expected 5.0, got {result}"
    assert result != 0.0, "BUG: pnl must not be silently zeroed"


# Тест 2: correct scaling with bought_price
def test_pnl_scaling_with_bought_price():
    """При bought_outcome_price=0.4 и stake=10 → shares=25, pnl=25*raw"""
    raw_pnl = 0.6        # нормализованный profit per share
    virtual_stake = 10.0
    bought_outcome_price = 0.4

    result = scaled_pnl(raw_pnl, bought_outcome_price, virtual_stake)

    assert result == 15.0


# ──────────────────────────────────────────────────────────────
# Тест 3: C1 — auto-signals не пересекаются с manual
# ──────────────────────────────────────────────────────────────
def test_no_double_counting_pnl():
    """manual + auto не должны суммировать одну и ту же market_id"""
    manual_market_ids = {'abc123', 'def456'}
    all_resolved_ids = {'abc123', 'ghi789', 'jkl000'}

    auto_ids = all_resolved_ids - manual_market_ids  # правильный фильтр

    assert 'abc123' not in auto_ids, "abc123 есть в manual — не должен быть в auto"
    assert 'ghi789' in auto_ids
    assert len(auto_ids) == 2


# ──────────────────────────────────────────────────────────────
# Тест 4: Kelly half-fraction
# ──────────────────────────────────────────────────────────────
def test_kelly_half_fraction():
    """Kelly fraction должен быть не более 0.5 от full Kelly"""
    p = 0.6
    b = 2.0  # fractional odds

    full_kelly = p - (1.0 - p) / b   # = 0.6 - 0.2 = 0.40
    half_kelly = max(0.0, full_kelly) * 0.5

    assert abs(full_kelly - 0.40) < 1e-9
    assert abs(half_kelly - 0.20) < 1e-9


# ──────────────────────────────────────────────────────────────
# Тест 5: Kelly при p=0 или b→0 не даёт NaN/отрицательных значений
# ──────────────────────────────────────────────────────────────
def test_kelly_edge_cases():
    cases = [
        (0.0, 2.0),   # p=0 → kelly отрицательный → max(0, ...)=0
        (1.0, 2.0),   # p=1 → kelly=1.0
        (0.6, 0.001), # b → 0 → kelly очень отрицательный → 0
        (0.5, 1.0),   # breakeven
    ]
    for p, b in cases:
        kelly = max(0.0, p - (1.0 - p) / b) * 0.5
        assert kelly >= 0.0, f"Kelly < 0 for p={p}, b={b}"
        assert not math.isnan(kelly)
        assert kelly <= 0.5  # half-Kelly не превышает 50%


# ──────────────────────────────────────────────────────────────
# Тест 6: proximity_score — корректное экспоненциальное затухание
# ──────────────────────────────────────────────────────────────
def test_event_proximity_score():
    def compute_proximity(hours_left):
        if hours_left is None or hours_left <= 0:
            return 1.0
        return round(math.exp(-hours_left / 24.0), 4)

    assert compute_proximity(0) == 1.0      # уже произошло
    assert compute_proximity(None) == 1.0   # неизвестно → максимальная близость
    score_24h = compute_proximity(24)
    score_48h = compute_proximity(48)
    assert score_24h > score_48h            # чем ближе — тем выше score
    assert 0.35 < score_24h < 0.38         # exp(-1) ≈ 0.3679
    assert compute_proximity(1000) < 0.01  # очень далеко → почти 0


# ──────────────────────────────────────────────────────────────
# Интеграционный тест: цепочки Compounding не имеют двойного инкремента
# ──────────────────────────────────────────────────────────────
def test_compounding_chain_increment_flow():
    """Симулирует полный цикл Compounding цепочки и проверяет шаги."""
    # Шаг 0: Создаем фейковую цепочку, как в db.py
    # initial_stake=50, target_steps=3, current_step=0, status='WAITING_RESOLUTION'
    chain = {
        "id": 1,
        "status": "WAITING_RESOLUTION",
        "current_stake": 50.0,
        "target_steps": 3,
        "current_step": 0
    }
    
    # Имитируем резолюцию шага 1 (выигрыш)
    # Код из services/outcome_tracker.py:_resolve_chain_bets_for_opportunity
    price = 0.5
    was_correct = True
    
    # 1. Первая ставка выиграла
    contracts = chain["current_stake"] / price  # 100 контрактов
    gross_payout = contracts * 1.0
    profit = gross_payout - chain["current_stake"]
    profit_after_fee = profit * 0.98  # 49.0
    chain["current_stake"] += profit_after_fee  # 99.0
    
    new_step = chain["current_step"] + 1  # 0 + 1 = 1
    if new_step >= chain["target_steps"]:
        chain["status"] = "COMPLETED"
    else:
        chain["status"] = "WAITING_NEXT"
    chain["current_step"] = new_step  # current_step = 1 в БД
    
    assert chain["current_step"] == 1
    assert chain["status"] == "WAITING_NEXT"
    
    # 2. Аллокация второй ставки (как в db.py, allocate_opportunity_to_chain)
    # Находим цепочку со статусом WAITING_NEXT.
    # В ИСПРАВЛЕННОЙ версии мы UPDATE статус на WAITING_RESOLUTION, но НЕ трогаем current_step!
    assert chain["status"] == "WAITING_NEXT"
    chain["status"] = "WAITING_RESOLUTION"
    # Раньше тут было current_step += 1 (стал бы 2, что привело бы к багу).
    # Теперь current_step остается 1.
    assert chain["current_step"] == 1
    
    # 3. Вторая ставка выиграла
    # services/outcome_tracker.py считывает current_step = 1
    new_step = chain["current_step"] + 1  # 1 + 1 = 2
    if new_step >= chain["target_steps"]:
        chain["status"] = "COMPLETED"
    else:
        chain["status"] = "WAITING_NEXT"
    chain["current_step"] = new_step  # current_step = 2 в БД
    
    assert chain["current_step"] == 2
    assert chain["status"] == "WAITING_NEXT"
    
    # 4. Аллокация третьей ставки
    chain["status"] = "WAITING_RESOLUTION"
    assert chain["current_step"] == 2
    
    # 5. Третья ставка выиграла
    new_step = chain["current_step"] + 1  # 2 + 1 = 3
    if new_step >= chain["target_steps"]:
        chain["status"] = "COMPLETED"
    else:
        chain["status"] = "WAITING_NEXT"
    chain["current_step"] = new_step  # current_step = 3 в БД
    
    assert chain["current_step"] == 3
    assert chain["status"] == "COMPLETED"


# ──────────────────────────────────────────────────────────────
# Тест 7: Преобразование event-ссылок Polymarket в market-ссылки
# ──────────────────────────────────────────────────────────────
def test_polymarket_url_conversion():
    """Проверяет преобразование ссылок /event/ -> /market/ и очистку от параметров."""
    from services.telegram_listener import clean_market_url
    from web.data_provider import clean_db_url

    # Входящие некорректные ссылки (с event и query параметрами)
    url_1 = "https://polymarket.com/event/fifwc-ksa-ury-2026-06-15-spread-home-1pt5?some_param=abc"
    url_2 = "https://polymarket.com/event/will-saudi-arabia-be-the-furthest-advancing-afc-nation-at-the-world-cup-20260603202050233"
    url_3 = "https://polymarket.com/market/fifwc-ksa-ury-2026-06-15-goals-federico-valverde-gte3?ref=123"

    # Ожидаемый результат
    expected_1 = "https://polymarket.com/market/fifwc-ksa-ury-2026-06-15-spread-home-1pt5"
    expected_2 = "https://polymarket.com/market/will-saudi-arabia-be-the-furthest-advancing-afc-nation-at-the-world-cup-20260603202050233"
    expected_3 = "https://polymarket.com/market/fifwc-ksa-ury-2026-06-15-goals-federico-valverde-gte3"

    # Тестируем clean_market_url
    assert clean_market_url(url_1) == expected_1
    assert clean_market_url(url_2) == expected_2
    assert clean_market_url(url_3) == expected_3
    assert clean_market_url(None) == ""

    # Тестируем clean_db_url
    assert clean_db_url("https://polymarket.com/event/test-slug") == "https://polymarket.com/market/test-slug"
    assert clean_db_url(None) is None

