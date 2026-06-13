import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from agents.shared.python.db import init_db, get_connection, save_market
import config

def test_outcome_eval_loop_integration(tmp_path):
    # Настраиваем временный путь к БД
    test_db_path = tmp_path / "test_eval_loop.db"
    
    with patch("config.DB_PATH", test_db_path):
        # Инициализируем БД
        init_db()
        
        now = datetime.now(timezone.utc)
        close_time = now - timedelta(hours=1)
        
        # Добавим 15 разных рынков и 15 сигналов к ним в БД
        from core.models import Market
        
        # Сначала сохраняем рынки
        for i in range(15):
            market_id = f"0xabc_{i}"
            m = Market(
                id=market_id,
                platform="polymarket",
                title=f"Will test {i} pass?",
                description=f"A test market {i}",
                url=f"https://polymarket.com/market/test_{i}",
                outcome="unknown",
                price=0.6,
                close_time=close_time,
                condition_id=f"cond_abc_{i}",
                volume=10000.0,
                tokens=[f"token_yes_{i}", f"token_no_{i}"]
            )
            save_market(m)
            
        # Теперь открываем соединение один раз для вставки сигналов
        with get_connection() as conn:
            for i in range(15):
                market_id = f"0xabc_{i}"
                conn.execute("""
                    INSERT INTO signals (
                        id, type, market_id, platform, edge, confidence, priority, 
                        summary, details, status, created_at, predicted_probability, 
                        market_price_at_signal, edge_at_signal, strategy_type, close_time, target_outcome
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"sig_{i}", "scout", market_id, "polymarket", 0.1, 0.8, "HIGH",
                    "Summary", "Details", "PENDING", now.isoformat(), 0.7,
                    0.6, 0.1, "scout", close_time.isoformat(), "YES"
                ))
            
        # Настроим моки для PolymarketResolutionClient
        from core.eval.polymarket_resolution_client import MarketResolution
        
        def mock_fetch(market_id):
            return MarketResolution(
                condition_id=f"cond_abc_{market_id.split('_')[-1]}",
                is_resolved=True,
                winning_outcome="YES",
                resolution_price=1.0
            )
        
        with patch("core.eval.outcome_tracker.PolymarketResolutionClient") as MockClient:
            mock_client_instance = MockClient.return_value
            mock_client_instance.fetch_resolution.side_effect = mock_fetch
            
            # Запускаем OutcomeTracker
            from core.eval.outcome_tracker import OutcomeTracker
            tracker = OutcomeTracker()
            
            # Заменим DB_PATH в outcome_tracker на временный
            with patch("core.eval.outcome_tracker.DB_PATH", str(test_db_path)), \
                 patch("core.eval.metrics_repository.DB_PATH", str(test_db_path)), \
                 patch("core.eval.calibration_store.DB_PATH", str(test_db_path)):
                     
                stats = tracker.run_cycle()
                
                # Проверим результаты
                assert stats["checked"] == 15
                assert stats["resolved"] == 15
                assert stats["skipped"] == 0
                assert stats["errors"] == 0
                
                # Проверим, что status сигнала обновился на WIN
                with get_connection() as conn:
                    sig = conn.execute("SELECT status, resolution_outcome, was_profitable, pnl_realized FROM signals WHERE id='sig_0'").fetchone()
                    assert sig["status"] == "WIN"
                    assert sig["resolution_outcome"] == "YES"
                    assert sig["was_profitable"] == 1
                    assert sig["pnl_realized"] is not None
                    
                    # Проверим, что метрики стратегии были сохранены
                    metrics = conn.execute("SELECT strategy_type, total_signals, win_rate FROM strategy_metrics WHERE strategy_type='scout'").fetchone()
                    assert metrics is not None
                    assert metrics["total_signals"] == 15
                    assert metrics["win_rate"] == 1.0
                    
                    # Проверим, что калибровка сохранила предложение
                    calib = conn.execute("SELECT param_name, strategy_type, param_value, auto_applied FROM calibration_params").fetchall()
                    assert len(calib) > 0
                    
                    # Проверим, что статистика запуска OutcomeTracker сохранилась в memory
                    mem_val = conn.execute("SELECT value FROM memory WHERE key='outcome_tracker_last_run'").fetchone()
                    assert mem_val is not None
