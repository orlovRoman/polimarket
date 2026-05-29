import pytest
import inspect
from agents.shared.utils.language_guard import validate_russian_fields
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent

def test_validate_russian_fields():
    data = {
        "reasoning": "Привет, это правильный текст на русском.",
        "risk": "Some english words are allowed like YES and NO.",
        "oracle_risk": "Иероглифы здесь: 极"
    }
    # Должен обнаружить иероглифы в oracle_risk
    assert validate_russian_fields(data, ["reasoning", "risk"]) is None
    assert validate_russian_fields(data, ["reasoning", "risk", "oracle_risk"]) == "oracle_risk"

def test_scout_agent_attempts():
    src = inspect.getsource(ScoutAgent.estimate_market)
    assert "range(1)" in src
    assert "range(2)" not in src

def test_swing_agent_attempts():
    src = inspect.getsource(SwingAgent.estimate_market)
    assert "range(1)" in src
    assert "range(2)" not in src

def test_shadow_agent_attempts():
    src = inspect.getsource(ShadowAgent.analyze_idea)
    assert "range(1)" in src
    assert "range(2)" not in src
