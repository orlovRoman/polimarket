import unittest
from unittest.mock import patch, MagicMock
from core.onchain_gate import check_onchain_gate
from core.onchain_scorer import OnchainScore
import asyncio
from core.models import Market
from datetime import datetime, timezone

class TestPennyStocks(unittest.TestCase):

    def test_onchain_gate_bypass_for_penny_stocks(self):
        # Проверяем, что Onchain Gate пропускает рынок при tag = penny_stocks
        # независимо от объемов и китов
        res = check_onchain_gate(
            oc_score=None,
            market_id="penny_mkt_1",
            total_volume_usd=50.0,  # ОЧЕНЬ низкий объем
            market_tag="penny_stocks"
        )
        self.assertTrue(res.allow)
        self.assertEqual(res.blocked_by, "pass")
        self.assertIn("Penny Stocks: Onchain Gate отключен", res.reason)

    def test_db_penny_stocks_operations(self):
        # Проверяем CRUD-операции с Penny Stocks в БД
        from agents.shared.python.db import (
            init_db,
            add_penny_stock_to_monitoring,
            get_active_penny_stocks,
            update_penny_stock_price,
            mark_penny_spike_sent,
            resolve_penny_stock,
            get_penny_stocks_stats,
            get_penny_stocks_history,
            get_connection
        )
        init_db()

        # Очистим тестовую таблицу перед тестом
        with get_connection() as conn:
            conn.execute("DELETE FROM penny_stocks_monitoring")

        # Добавляем рынок
        add_penny_stock_to_monitoring(
            market_id="p1",
            title="Will test pass?",
            url="http://test.com/p1",
            initial_price=0.03,
            predicted_outcome="YES",
            edge=0.15,
            confidence=0.8
        )

        active = get_active_penny_stocks()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["market_id"], "p1")
        self.assertEqual(active[0]["initial_price"], 0.03)
        self.assertEqual(active[0]["current_price"], 0.03)
        self.assertEqual(active[0]["predicted_outcome"], "YES")
        self.assertEqual(active[0]["spike_alert_sent"], 0)

        # Обновляем цену (всплеск)
        update_penny_stock_price("p1", 0.07, 1200.0)
        active = get_active_penny_stocks()
        self.assertEqual(active[0]["current_price"], 0.07)
        self.assertEqual(active[0]["max_price_seen"], 0.07)
        self.assertEqual(active[0]["volume_2h"], 1200.0)

        # Помечаем алерт как отправленный
        mark_penny_spike_sent("p1")
        active = get_active_penny_stocks()
        self.assertEqual(active[0]["spike_alert_sent"], 1)

        # Разрешаем исход (совпадает с прогнозом)
        resolve_penny_stock("p1", "YES")
        active = get_active_penny_stocks()
        self.assertEqual(len(active), 0) # Больше не активен

        history = get_penny_stocks_history(10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["actual_outcome"], "YES")

        # Проверяем статистику
        stats = get_penny_stocks_stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["resolved"], 1)
        self.assertEqual(stats["correct"], 1)
        self.assertEqual(stats["win_rate"], 1.0)
        self.assertAlmostEqual(stats["avg_edge"], 0.15)

    @patch("core.smart_money.fetch_smart_money_sync")
    @patch("core.onchain_gate.check_onchain_gate")
    @patch("core.price_velocity.detect_velocity_anomaly")
    @patch("core.workflow.get_memory")
    @patch("config.llm_health_gate")
    @patch("core.workflow.build_search_query")
    @patch("core.workflow._fetch_grounded_context")
    def test_run_agent_evaluation_propagates_scan_category(
        self, mock_grounded, mock_search_query, mock_llm_health, mock_get_memory,
        mock_velocity, mock_gate, mock_fetch_sm
    ):
        from core.workflow import run_agent_evaluation
        from core.onchain_gate import GateResult
        
        # Настройка моков для обхода LLM и дедупликации
        mock_get_memory.return_value = None
        mock_llm_health.check_availability.return_value = True
        mock_velocity.return_value = MagicMock(has_anomaly=False)
        mock_fetch_sm.return_value = None
        mock_gate.return_value = GateResult(allow=False, reason="Blocked", blocked_by="whales")
        mock_search_query.return_value = "test query"
        mock_grounded.return_value = "test grounded"
        
        # Создаем фиктивный рынок
        m = Market(
            id="p_mkt_eval",
            platform="polymarket",
            title="Test Penny Eval",
            url="http://test.com",
            outcome="YES",
            price=0.02,
            close_time=datetime.now(timezone.utc),
            condition_id="cond_p"
        )
        
        scout = MagicMock()
        swing = MagicMock()
        update_state = MagicMock()
        
        asyncio.run(run_agent_evaluation(
            m, scout, swing, update_state,
            scan_category="penny_stocks"
        ))
        
        # Проверяем, что в check_onchain_gate был проброшен market_tag="penny_stocks"
        mock_gate.assert_called_once()
        args, kwargs = mock_gate.call_args
        market_tag_val = kwargs.get("market_tag") or (args[3] if len(args) > 3 else None)
        self.assertEqual(market_tag_val, "penny_stocks")

if __name__ == '__main__':
    unittest.main()
