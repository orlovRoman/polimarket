import pytest
from agents.polymarket_swing_agent.src.agent import _safe_float

@pytest.mark.parametrize("val,default,expected", [
    ("",       0.5,  0.5),
    (None,     0.5,  0.5),
    ("0.75",   0.5,  0.75),
    (0.8,      0.5,  0.8),
    ("abc",    0.5,  0.5),
    ("0",      0.5,  0.0),
])
def test_safe_float(val, default, expected):
    assert _safe_float(val, default) == expected

def test_swing_agent_empty_string_no_crash():
    """LLM вернул '' в числовых полях — агент не крашится, использует дефолты."""
    analysis = {
        "llm_confidence": "",
        "target_exit_price": "",
        "asymmetry_score": "",
        "llm_direction": "YES",
    }
    assert _safe_float(analysis.get("llm_confidence"), 0.5)   == 0.5
    assert _safe_float(analysis.get("target_exit_price"), 0.15) == 0.15
    assert _safe_float(analysis.get("asymmetry_score"), 0.5)  == 0.5
