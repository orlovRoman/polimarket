"""Тесты для багов из коммитов c00ea2f и 8d6df98."""
import pytest
from unittest.mock import patch


# ── БАГ 1: статус NEW не должен резолвиться как торговая позиция ─────────────

class TestNewStatusNotResolved:

    @patch("services.outcome_tracker._fetch_resolution", return_value="YES")
    @patch("services.outcome_tracker._resolve_signal")
    @patch("agents.shared.python.db.resolve_compound_opportunity")
    @patch("agents.shared.python.db.get_compound_settings",
           return_value={"virtual_stake": 50.0})
    @patch("agents.shared.python.db.get_connection")
    def test_new_status_does_not_trigger_resolve_signal(
        self, mock_conn, mock_cfg, mock_resolve_opp, mock_resolve_sig, mock_fetch
    ):
        """NEW-позиция не должна вызывать _resolve_signal — у неё нет сигнала."""
        opp = {
            "id": "new-opp-001",
            "market_id": "0xNEW001",
            "status": "NEW",            # ← только что найдена, не куплена
            "price": 0.85,
            "outcome": "YES",
            "virtual_bought_price": None,
            "market_resolved_outcome": "YES",
        }

        class FakeCursor:
            def __init__(self, data):
                self.data = data
            def fetchall(self):
                return self.data
            def fetchone(self):
                # ПРАВИЛЬНО: возвращаем первый элемент, а не весь список
                if isinstance(self.data, list):
                    return self.data[0] if self.data else None
                return self.data

        def smart_execute(query, params=()):
            if "compound_opportunities" in query:
                return FakeCursor([opp])
            elif "signals" in query:
                return FakeCursor(None)
            elif "memory" in query:
                return FakeCursor(None)
            return FakeCursor([])

        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.side_effect = smart_execute

        from services.outcome_tracker import _resolve_compound_outcomes
        _resolve_compound_outcomes()

        mock_resolve_sig.assert_not_called(), \
            "_resolve_signal вызван для NEW-статуса — это ложный PnL!"

    @patch("services.outcome_tracker._fetch_resolution", return_value="YES")
    @patch("services.outcome_tracker._resolve_signal")
    @patch("agents.shared.python.db.resolve_compound_opportunity")
    @patch("agents.shared.python.db.get_compound_settings",
           return_value={"virtual_stake": 50.0})
    @patch("agents.shared.python.db.get_connection")
    def test_bought_status_does_trigger_resolve_signal(
        self, mock_conn, mock_cfg, mock_resolve_opp, mock_resolve_sig, mock_fetch
    ):
        """BOUGHT-позиция должна вызывать _resolve_signal — регресс-тест."""
        opp = {
            "id": "bought-opp-001",
            "market_id": "0xBOUGHT001",
            "status": "BOUGHT",
            "price": 0.85,
            "outcome": "YES",
            "virtual_bought_price": None,
            "market_resolved_outcome": "YES",
        }
        fake_sig = {
            "id": "sig-001", "market_id": "0xBOUGHT001",
            "strategy_type": "FAVOURITE_COMPOUND", "target_outcome": "YES",
            "edge": 0.05, "confidence": 0.85, "created_at": "2026-06-01",
            "estimated_probability": 0.85, "market_price_at_signal": 0.82,
            "market_title": "Test Bought Market",
        }

        class FakeCursor:
            def __init__(self, data):
                self.data = data
            def fetchall(self):
                return self.data
            def fetchone(self):
                if isinstance(self.data, list):
                    return self.data[0] if self.data else None
                return self.data

        def smart_execute(query, params=()):
            if "compound_opportunities" in query:
                return FakeCursor([opp])
            elif "signals" in query:
                return FakeCursor(fake_sig)
            elif "memory" in query:
                return FakeCursor(None)
            return FakeCursor([])

        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.side_effect = smart_execute

        from services.outcome_tracker import _resolve_compound_outcomes
        _resolve_compound_outcomes()

        mock_resolve_sig.assert_called_once(), \
            "_resolve_signal НЕ вызван для BOUGHT-статуса — регресс!"


# ── БАГ 2: FakeCursor.fetchone() должен возвращать dict, не list ─────────────

class TestFakeCursorFetchoneContract:

    def test_fetchone_returns_single_dict_not_list(self):
        """fetchone() должен вернуть dict, а не весь список."""
        class FakeCursorFixed:
            def __init__(self, data):
                self.data = data
            def fetchone(self):
                if isinstance(self.data, list):
                    return self.data[0] if self.data else None
                return self.data

        row = {"id": "sig-1", "strategy_type": "FAVOURITE_COMPOUND"}
        result = FakeCursorFixed([row]).fetchone()
        assert isinstance(result, dict), \
            f"fetchone() вернул {type(result)}, ожидался dict"
        assert result["strategy_type"] == "FAVOURITE_COMPOUND"

    def test_old_fakecursor_bug_reproduces(self):
        """Документирует баг: старый FakeCursor.fetchone() возвращал список."""
        class FakeCursorBuggy:
            def __init__(self, data):
                self.data = data
            def fetchone(self):
                return self.data  # БАГ: возвращает весь список

        row = {"id": "sig-1", "strategy_type": "FAVOURITE_COMPOUND"}
        result = FakeCursorBuggy([row]).fetchone()
        assert isinstance(result, list), "Тест документирует старый баг"
        with pytest.raises(TypeError):
            _ = result["strategy_type"]  # list не поддерживает string-ключи


# ── БАГ 3: min_samples=5 тихо скрывает статистику агентов ───────────────────

class TestOrchestratorAccuracyContextMinSamples:

    @patch("agents.shared.python.db.get_agent_accuracy")
    def test_returns_none_when_below_min_samples(self, mock_get_acc):
        """evaluated_total=3 < min_samples=5 → None."""
        mock_get_acc.return_value = {"total": 3, "correct": 2, "incorrect": 1, "accuracy": 0.66}
        from agents.shared.python.db import get_agent_accuracy_context

        result = get_agent_accuracy_context("scout", min_samples=5)
        assert result is None, \
            f"Ожидался None при 3 < 5 семплах, получено: {result!r}"

    @patch("agents.shared.python.db.get_agent_accuracy")
    def test_returns_context_when_at_min_samples(self, mock_get_acc):
        """evaluated_total=5 == min_samples=5 → должен вернуть строку."""
        mock_get_acc.return_value = {"total": 5, "correct": 4, "incorrect": 1, "accuracy": 0.8}
        from agents.shared.python.db import get_agent_accuracy_context

        result = get_agent_accuracy_context("scout", min_samples=5)
        assert result is not None, "При ровно 5 семплах должен вернуть контекст"
        assert isinstance(result, str)

    @patch("agents.shared.python.db.get_agent_accuracy")
    def test_min_samples_1_always_returned_context(self, mock_get_acc):
        """Регресс: min_samples=1 + 1 семпл → должен вернуть контекст."""
        mock_get_acc.return_value = {"total": 1, "correct": 1, "incorrect": 0, "accuracy": 1.0}
        from agents.shared.python.db import get_agent_accuracy_context

        result = get_agent_accuracy_context("scout", min_samples=1)
        assert result is not None
        assert isinstance(result, str)
