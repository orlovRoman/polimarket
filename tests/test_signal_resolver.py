import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from agents.shared.python.db import init_db, get_connection
from core.eval.signal_logger import SignalLogger, StrategyType
from services.signal_resolver import resolve_pending_signals

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    
    import config
    import agents.shared.python.db as db_module
    import core.eval.signal_logger as sl_module
    
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(db_module, "DB_PATH", test_db)
    monkeypatch.setattr(sl_module, "DB_PATH", test_db)
    
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    from agents.shared.python.db import init_db
    init_db()
    yield

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
            "VALUES (?, 'polymarket', 'Middle Price Market', '', 'http://test.4', '', 0.50, ?, '[]', 1000.0)",
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
        row = conn.execute("SELECT status, was_profitable, resolved_at FROM signals WHERE id = 'sig-yes-win'").fetchone()
        assert row["status"] == "WIN"
        assert row["was_profitable"] == 1
        assert row["resolved_at"] is not None
        
        # sig-yes-loss -> LOSS
        row = conn.execute("SELECT status, was_profitable, resolved_at FROM signals WHERE id = 'sig-yes-loss'").fetchone()
        assert row["status"] == "LOSS"
        assert row["was_profitable"] == 0
        assert row["resolved_at"] is not None

        # sig-no-win -> WIN
        row = conn.execute("SELECT status, was_profitable, resolved_at FROM signals WHERE id = 'sig-no-win'").fetchone()
        assert row["status"] == "WIN"
        assert row["was_profitable"] == 1
        assert row["resolved_at"] is not None

        # sig-future -> PENDING (не изменился)
        row = conn.execute("SELECT status, resolved_at FROM signals WHERE id = 'sig-future'").fetchone()
        assert row["status"] == "PENDING"
        assert row["resolved_at"] is None

        # sig-middle -> PENDING (не изменился)
        row = conn.execute("SELECT status, resolved_at FROM signals WHERE id = 'sig-middle'").fetchone()
        assert row["status"] == "PENDING"
        assert row["resolved_at"] is None


def test_log_signal_archives_old_pending():
    # Создаем один тестовый рынок
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES ('mkt-unique-test', 'polymarket', 'Unique Test Market', '', 'http://test.5', 'YES', 0.5, '2026-06-08 12:00:00', '[]', 1000.0)"
        )
        
    logger = SignalLogger()
    
    # Записываем первый сигнал по этому рынку (статус PENDING)
    logger.log_signal(
        signal_id="sig-first-pending",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-unique-test",
        predicted_probability=0.7,
        market_price_at_signal=0.5,
        edge_at_signal=0.2,
        metadata={"target_outcome": "YES"}
    )
    
    # Записываем второй сигнал по тому же рынку (тоже должен быть PENDING)
    # Это должно отработать без IntegrityError благодаря авто-архивации старого сигнала
    logger.log_signal(
        signal_id="sig-second-pending",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-unique-test",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "YES"}
    )
    
    # Проверяем статусы сигналов в БД:
    # Первый должен стать ARCHIVED, а второй остаться PENDING
    with get_connection() as conn:
        row1 = conn.execute("SELECT status FROM signals WHERE id = 'sig-first-pending'").fetchone()
        assert row1["status"] == "ARCHIVED"
        
        row2 = conn.execute("SELECT status FROM signals WHERE id = 'sig-second-pending'").fetchone()
        assert row2["status"] == "PENDING"


def test_resolve_is_idempotent():
    now = datetime.now(timezone.utc)
    expired_time = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume) "
            "VALUES (?, 'polymarket', 'Expired Market for Idempotency', '', 'http://test.idemp', 'YES', 0.98, ?, '[]', 1000.0)",
            ("mkt-idempotent", expired_time)
        )
        
    logger = SignalLogger()
    
    logger.log_signal(
        signal_id="sig-idempotent",
        strategy_type=StrategyType.SCOUT,
        market_id="mkt-idempotent",
        predicted_probability=0.8,
        market_price_at_signal=0.5,
        edge_at_signal=0.3,
        metadata={"target_outcome": "YES"}
    )
    
    # Первый запуск - должен разрешить 1 сигнал
    count1 = resolve_pending_signals()
    assert count1 == 1
    
    with get_connection() as conn:
        row1 = conn.execute("SELECT status, resolved_at FROM signals WHERE id = 'sig-idempotent'").fetchone()
        assert row1["status"] == "WIN"
        first_resolved_at = row1["resolved_at"]
        assert first_resolved_at is not None
        
    # Второй запуск - должен обработать 0 сигналов, статус не меняется
    count2 = resolve_pending_signals()
    assert count2 == 0
    
    with get_connection() as conn:
        row2 = conn.execute("SELECT status, resolved_at FROM signals WHERE id = 'sig-idempotent'").fetchone()
        assert row2["status"] == "WIN"
        assert row2["resolved_at"] == first_resolved_at
