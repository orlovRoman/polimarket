import pytest
from datetime import datetime
from core.guards import LLMHealthGate, LLMUnavailableError

class TestHealthGate403:

    def test_403_counted_as_rate_limit(self):
        """403 должен считаться как rate-limit ошибка."""
        gate = LLMHealthGate()
        for _ in range(3):
            gate.record_error(403)
        assert gate.state == "DEGRADED", (
            f"После 3 x 403 ожидается DEGRADED, получено: {gate.state}"
        )

    def test_403_causes_dead_at_threshold(self):
        """5 ошибок 403 → DEAD."""
        gate = LLMHealthGate()
        for _ in range(5):
            gate.record_error(403)
        assert gate.state == "DEAD"

    def test_mixed_403_429_accumulate(self):
        """403 и 429 вместе должны накапливаться."""
        gate = LLMHealthGate()
        gate.record_error(429)
        gate.record_error(429)
        gate.record_error(403)  # ← третья ошибка → DEGRADED
        assert gate.state == "DEGRADED"

    def test_500_still_ignored(self):
        """500 не должен влиять на состояние gate."""
        gate = LLMHealthGate()
        for _ in range(10):
            gate.record_error(500)
        assert gate.state == "HEALTHY"

    def test_403_triggers_fast_fallback_via_dead(self):
        """После 5 x 403 check_availability() бросает LLMUnavailableError."""
        gate = LLMHealthGate()
        for _ in range(5):
            gate.record_error(403)
        with pytest.raises(LLMUnavailableError):
            gate.check_availability()
