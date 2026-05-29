import pytest
import inspect
from core.swing_rules import swing_decision
from agents.polymarket_swing_agent.src.agent import SwingAgent

@pytest.mark.parametrize("hype,price,expected_rec", [
    (0.75, 0.10, "buy"),    # дёшево + высокий хайп
    (0.75, 0.50, "ignore"), # дорого — нет смысла
    (0.40, 0.08, "ignore"), # дёшево, но хайпа мало
    (0.60, 0.12, "buy"),    # граничный случай
])
def test_swing_decision(hype, price, expected_rec):
    rec, conf = swing_decision(hype, price)
    assert rec == expected_rec
    assert 0.0 <= conf <= 1.0

def test_llm_schema_has_no_hype_potential():
    """Schema больше не запрашивает hype_potential у LLM."""
    src = inspect.getsource(SwingAgent.estimate_market)
    # Ищем блок объявления schema и проверяем отсутствие hype_potential в ней
    schema_part = src.split('schema =')[1].split('payload =')[0]
    assert 'hype_potential' not in schema_part
    assert 'recommendation' not in schema_part
    assert 'confidence' not in schema_part
