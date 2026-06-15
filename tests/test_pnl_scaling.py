import pytest

def scaled_pnl(pnl_raw: float, bought_price: float | None, stake: float) -> float:
    """Зеркало формулы из web/data_provider.py::_process_resolved_row."""
    if bought_price and 0.0 < bought_price < 1.0:
        return round((stake / bought_price) * pnl_raw, 2)
    return 0.0

@pytest.mark.parametrize("pnl_raw, bought_price, stake, expected", [
    # Penny stock WIN: купили по 0.05, прибыль = 0.95 на контракт
    (0.95,  0.05,   250.0,  4750.0),
    # Penny stock LOSS: купили по 0.05, потеряли 1.0 на контракт  
    (-1.0,  0.05,   250.0,  -5000.0),
    # Нормальный рынок: купили по 0.60
    (0.40,  0.60,   250.0,  pytest.approx(166.67, abs=0.01)),
    # bought_price == 0 → защита от деления на 0
    (0.95,  0.0,    250.0,  0.0),
    # bought_price is None
    (0.95,  None,   250.0,  0.0),
    # bought_price == 1.0 → граница условия (< 1.0), не масштабируем
    (0.95,  1.0,    250.0,  0.0),
    # bought_price > 1.0 → невалидная цена, 0
    (0.95,  1.5,    250.0,  0.0),
    # pnl_raw == 0
    (0.0,   0.05,   250.0,  0.0),
])
def test_pnl_formula(pnl_raw, bought_price, stake, expected):
    assert scaled_pnl(pnl_raw, bought_price, stake) == expected

def test_no_double_scaling():
    """
    Двойное масштабирование: если stake=250, price=0.05, pnl=0.95
    неверный результат = stake * (stake/price) * pnl = 1_187_500
    верный результат   = (stake/price) * pnl         = 4_750
    """
    stake, price, pnl = 250.0, 0.05, 0.95
    correct = scaled_pnl(pnl, price, stake)
    double  = round((stake * stake / price) * pnl, 2)
    
    assert correct == pytest.approx(4750.0), f"Ожидали 4750, получили {correct}"
    assert double  == pytest.approx(1_187_500.0)
    assert correct != double, "Формула применяется дважды!"

def test_pnl_symmetry():
    """WIN + LOSS на одной цене должны давать почти симметричный результат."""
    stake, price = 250.0, 0.10
    win  = scaled_pnl(0.90, price, stake)   # контракт вырос 0.10 → 1.0
    loss = scaled_pnl(-1.0, price, stake)  # контракт упал в 0
    # WIN ~2250, LOSS ~-2500 — не идеально симметрично (разная база), но проверим знак
    assert win > 0
    assert loss < 0
