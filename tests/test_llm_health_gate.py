import pytest
import time
from datetime import datetime, timedelta
from core.guards import LLMHealthGate, LLMUnavailableError

class TestLLMHealthGate:

    def _gate(self) -> LLMHealthGate:
        return LLMHealthGate()

    def test_healthy_returns_true(self):
        gate = self._gate()
        assert gate.check_availability() is True

    def test_degraded_returns_false_not_raises(self):
        """DEGRADED должен вернуть False, а НЕ бросить исключение."""
        gate = self._gate()
        gate._force_degraded()  # см. ниже — нужно добавить в guards.py
        result = gate.check_availability()
        assert result is False, "DEGRADED должен вернуть False, не бросать исключение"

    def test_dead_raises_llm_unavailable(self):
        """DEAD + пауза не истекла → LLMUnavailableError."""
        gate = self._gate()
        gate._force_dead()
        with pytest.raises(LLMUnavailableError):
            gate.check_availability()

    def test_dead_after_pause_returns_true(self):
        """DEAD, но пауза истекла → возвращает True (даём шанс)."""
        gate = self._gate()
        gate._force_dead()
        # Симулируем истечение паузы
        with gate.lock:
            gate.retry_after = datetime.now() - timedelta(seconds=1)
        result = gate.check_availability()
        assert result is True
        assert gate.state == "HEALTHY"

    def test_degraded_after_pause_returns_true(self):
        """DEGRADED, пауза истекла → True и сброс в HEALTHY."""
        gate = self._gate()
        gate._force_degraded()
        with gate.lock:
            gate.retry_after = datetime.now() - timedelta(seconds=1)
        result = gate.check_availability()
        assert result is True
        assert gate.state == "HEALTHY"

    def test_error_accumulation_transitions(self):
        """3 ошибки → DEGRADED, 5 ошибок → DEAD."""
        gate = self._gate()
        for _ in range(3):
            gate.record_error(429)
        assert gate.state == "DEGRADED"
        for _ in range(2):
            gate.record_error(429)
        assert gate.state == "DEAD"

    def test_non_rate_limit_errors_ignored(self):
        """Ошибки 500, 400 не считаются — состояние не меняется."""
        gate = self._gate()
        for _ in range(10):
            gate.record_error(500)
        assert gate.state == "HEALTHY"

    def test_success_resets_state(self):
        """record_success() сбрасывает DEGRADED → HEALTHY."""
        gate = self._gate()
        gate._force_degraded()
        gate.record_success()
        assert gate.state == "HEALTHY"
        assert gate.error_timestamps == []
