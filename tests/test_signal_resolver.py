import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from agents.shared.python.db import init_db, get_connection
from core.eval.signal_logger import SignalLogger, StrategyType
from services.signal_resolver import resolve_pending_signals

@pytest.fixture(autouse=True)
def setup_test_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM markets")

def test_resolve_pending_signals_logic():
    # Создаем тестовые рынки в БД
    now = datetime.now(timezone.utc)
    expired_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    future_time = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        # 1. Рынок истек, цена YES = 0.98 (победа YES)
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Expired YES Market 1', '', 'http://test.1', 'YES', 0.98, ?, '[]', 1000.0)",
            ("mkt-expired-yes", expired_time)
        )
        # 1b. Еще один рынок истек, цена YES = 0.98 (победа YES)
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Expired YES Market 2', '', 'http://test.1b', 'YES', 0.98, ?, '[]', 1000.0)",
            ("mkt-expired-yes-loss", expired_time)
        )
        # 2. Рынок истек, цена YES = 0.02 (победа NO)
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Expired NO Market', '', 'http://test.2', 'NO', 0.02, ?, '[]', 1000.0)",
            ("mkt-expired-no", expired_time)
        )
        # 3. Рынок не истек, цена YES = 0.98 (не должен резолвиться, т.к. время в будущем)
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Future Market', '', 'http://test.3', 'YES', 0.98, ?, '[]', 1000.0)",
            ("mkt-future", future_time)
        )
        # 4. Рынок истек, но цена посередине = 0.50 (не должен резолвиться)
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Middle Price Market', '', 'http://test.4', 'YES', 0.50, ?, '[]', 1000.0)",
            ("mkt-middle", expired_time)
        )
        
    # Создаем сигналы
    logger = SignalLogger()
    
    # Сигнал 1: на истекшем рынке с победой YES, цель YES -> WIN
    logger.log_signal(
        signal_id="sig-yes-win",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-expired-yes",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "YES"}
    )
    # Сигнал 2: на истекшем рынке с победой YES, цель NO -> LOSS
    logger.log_signal(
        signal_id="sig-yes-loss",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-expired-yes-loss",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "NO"}
    )
    # Сигнал 3: на истекшем рынке с победой NO, цель NO -> WIN
    logger.log_signal(
        signal_id="sig-no-win",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-expired-no",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "NO"}
    )
    # Сигнал 4: на будущем рынке (не должен разрешиться)
    logger.log_signal(
        signal_id="sig-future",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-future",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "YES"}
    )
    # Сигнал 5: на рынке с ценой 0.50 (не должен разрешиться)
    logger.log_signal(
        signal_id="sig-middle",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-middle",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "YES"}
    )

    # Запускаем авторезолюцию
    count = resolve_pending_signals()
    assert count == 3  # sig-yes-win, sig-yes-loss, sig-no-win

    # Проверяем статусы сигналов в БД
    with get_connection() as conn:
        # sig-yes-win -> WIN
        row = conn.execute("SELECT status, was_profitable FROM signals WHERE id = 'sig-yes-win'").fetchone()
        assert row["status"] == "WIN"
        assert row["was_profitable"] == 1
        
        # sig-yes-loss -> LOSS
        row = conn.execute("SELECT status, was_profitable FROM signals WHERE id = 'sig-yes-loss'").fetchone()
        assert row["status"] == "LOSS"
        assert row["was_profitable"] == 0

        # sig-no-win -> WIN
        row = conn.execute("SELECT status, was_profitable FROM signals WHERE id = 'sig-no-win'").fetchone()
        assert row["status"] == "WIN"
        assert row["was_profitable"] == 1

        # sig-future -> PENDING (не изменился)
        row = conn.execute("SELECT status FROM signals WHERE id = 'sig-future'").fetchone()
        assert row["status"] == "PENDING"

        # sig-middle -> PENDING (не изменился)
        row = conn.execute("SELECT status FROM signals WHERE id = 'sig-middle'").fetchone()
        assert row["status"] == "PENDING"
