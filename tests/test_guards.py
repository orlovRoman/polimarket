import pytest
import time
from datetime import datetime, timedelta, timezone
from core.guards import LLMHealthGate, LLMUnavailableError

def test_gate_becomes_dead_after_5_errors():
    gate = LLMHealthGate()
    gate.window_sec = 60 # убедимся что окно достаточное
    for _ in range(5):
        gate.record_error(429)
    assert gate.state == "DEAD"
    assert gate.retry_after > datetime.now(timezone.utc)
    
    with pytest.raises(LLMUnavailableError):
        gate.check_availability()

def test_gate_becomes_degraded_after_3_errors():
    gate = LLMHealthGate()
    for _ in range(3):
        gate.record_error(429)
    assert gate.state == "DEGRADED"
    # Для DEGRADED мы все равно позволяем check_availability проходить (partial-open или игнор)
    assert gate.check_availability() == False

def test_gate_recovers_after_backoff():
    gate = LLMHealthGate()
    gate._force_dead()
    
    # Мокаем retry_after в прошлое с таймзоной
    gate.retry_after = datetime.now(timezone.utc) - timedelta(seconds=10)
    assert gate.check_availability() == True
    assert gate.state == "HEALTHY"

def test_gate_records_success():
    gate = LLMHealthGate()
    for _ in range(3):
        gate.record_error(503)
    assert gate.state == "DEGRADED"
    
    gate.record_success()
    assert gate.state == "HEALTHY"
    assert len(gate.error_timestamps) == 0

def test_llm_unavailable_error_agent_name():
    err = LLMUnavailableError("API is down", agent_name="SCOUT")
    assert err.agent_name == "SCOUT"
    assert str(err) == "API is down"

def test_with_retry_injects_agent_name():
    from agents.shared.python.llm_wrapper import with_retry
    from config import llm_health_gate
    
    # Сбросим состояние
    llm_health_gate.record_success()
    
    class DummyAgent:
        def __init__(self):
            self.name = "TEST_AGENT"
            
        @with_retry(max_attempts=1)
        def failing_call(self):
            raise ValueError("Direct failure")

    agent = DummyAgent()
    with pytest.raises(LLMUnavailableError) as exc_info:
        agent.failing_call()
        
    assert exc_info.value.agent_name == "TEST_AGENT"

def test_provider_keys_fallback():
    from agents.shared.utils.gemini_client import generate_content_with_fallback, PROVIDERS_CONFIG
    from unittest.mock import MagicMock
    
    mock_send = MagicMock()
    mock_send.side_effect = [
        ValueError("Key 1 Rate Limit / Error"),
        ({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}, 10, 20)
    ]
    
    original_send = PROVIDERS_CONFIG["gemini"]["send_func"]
    original_keys = PROVIDERS_CONFIG["gemini"]["keys"]
    
    PROVIDERS_CONFIG["gemini"]["send_func"] = mock_send
    PROVIDERS_CONFIG["gemini"]["keys"] = ["key_1", "key_2"]
    
    try:
        payload = {"contents": [{"parts": [{"text": "dummy"}]}]}
        res, model = generate_content_with_fallback(
            api_key="key_1",
            payload=payload,
            default_model="gemini-2.5-flash",
            agent_name="TEST_FALLBACK"
        )
        
        assert mock_send.call_count == 2
        assert mock_send.call_args_list[0][0][2] == "key_1"
        assert mock_send.call_args_list[1][0][2] == "key_2"
        
    finally:
        PROVIDERS_CONFIG["gemini"]["send_func"] = original_send
        PROVIDERS_CONFIG["gemini"]["keys"] = original_keys

