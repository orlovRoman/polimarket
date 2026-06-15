# tests/test_remaining_bugs.py
"""
Регрессионные тесты для багов, НЕ покрытых в ae8564/7de0eb1.
"""
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────────────────────
# Фикстура: in-memory БД со схемой whale + compound
# ──────────────────────────────────────────────────────────────
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE markets (
            id TEXT PRIMARY KEY,
            platform TEXT,
            title TEXT,
            url TEXT,
            outcome TEXT,
            price REAL,
            close_time TEXT
        );
        CREATE TABLE penny_stocks_monitoring (
            market_id TEXT PRIMARY KEY, title TEXT, url TEXT, initial_price REAL, current_price REAL, max_price_seen REAL, min_price_seen REAL,
            predicted_outcome TEXT, edge REAL, confidence REAL, status TEXT, resolved_at TEXT, actual_outcome TEXT, close_time TEXT
        );
        CREATE TABLE whale_stocks_monitoring (
            market_id TEXT PRIMARY KEY, title TEXT, url TEXT, initial_price REAL, current_price REAL, max_price_seen REAL, min_price_seen REAL,
            predicted_outcome TEXT, edge REAL, confidence REAL, status TEXT, resolved_at TEXT, actual_outcome TEXT, wallet_address TEXT, close_time TEXT
        );
        CREATE TABLE whale_virtual_trades_history (
            id INTEGER PRIMARY KEY,
            market_id TEXT,
            pnl_cents REAL,
            bought_outcome_price REAL,
            sold_at TEXT
        );
        CREATE TABLE compound_settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE global_settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO global_settings VALUES ('max_concurrent_chains', '10');
        INSERT INTO global_settings VALUES ('virtual_stake', '100.0');
    """)
    yield conn
    conn.close()


# ──────────────────────────────────────────────────────────────
# Баг 1: close_time без timezone offset → сравнение с aware datetime упадёт
# ──────────────────────────────────────────────────────────────
class TestCloseTimeTimezone:
    def test_penny_close_time_is_parseable_as_aware(self, db):
        """close_time должен быть ISO-строкой с UTC-суффиксом или без,
        но при парсинге не должен вызывать TypeError."""
        from agents.shared.python.db import add_penny_stock_to_monitoring

        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_penny_stock_to_monitoring(
                market_id="test-tz-1",
                title="TZ Test",
                url="https://example.com",
                initial_price=0.4,
                close_time=None,
            )

        row = db.execute(
            "SELECT * FROM markets WHERE id='test-tz-1'"
        ).fetchone()
        print("DEBUG ROW:", dict(row) if row else None)
        assert row is not None
        ct_str = row["close_time"]
        # Должен парситься fromisoformat без падения
        ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        # Если строка без timezone — это тихий баг: сравнение упадёт
        assert ct.replace(tzinfo=timezone.utc) > now_utc, (
            f"close_time={ct_str!r} должен быть в будущем"
        )

    def test_whale_close_time_is_parseable_as_aware(self, db):
        """Аналогичная проверка для whale_stocks_monitoring."""
        from agents.shared.python.db import add_whale_stock_to_monitoring

        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_whale_stock_to_monitoring(
                market_id="test-whale-tz-1",
                title="Whale TZ Test",
                url="https://example.com",
                initial_price=0.6,
                close_time=None,
                wallet_address="0xABC",
            )

        row = db.execute(
            "SELECT * FROM markets WHERE id='test-whale-tz-1'"
        ).fetchone()
        print("DEBUG ROW WHALE:", dict(row) if row else None)
        assert row is not None
        ct_str = row["close_time"]
        ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
        assert ct.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────
# Баг 2: get_curve_for_strategy whale — NULL bought_outcome_price
#         не должен создавать «нулевые» точки в кривой
# ──────────────────────────────────────────────────────────────
class TestWhaleCurveNullBoughtPrice:
    def _insert_trade(self, db, bought_price, pnl_cents, days_ago):
        dt = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        db.execute(
            "INSERT INTO whale_virtual_trades_history VALUES (NULL,?,?,?,?)",
            ("mkt-1", pnl_cents, bought_price, dt),
        )
        db.commit()

    def test_null_bought_price_excluded_from_curve(self, db):
        """Сделки с NULL bought_outcome_price не должны создавать точки в кривой."""
        # Один нормальный трейд 7 дней назад
        self._insert_trade(db, bought_price=0.6, pnl_cents=10.0, days_ago=7)
        # Один трейд с NULL (сломанная запись) сегодня
        self._insert_trade(db, bought_price=None, pnl_cents=5.0, days_ago=0)

        from web.data_provider import get_equity_curve
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            curve = get_equity_curve(days=30, strategy="whale")

        # Точек в кривой должна быть ровно 1 (только нормальный трейд)
        assert len(curve) == 1, (
            f"Ожидали 1 точку в кривой, получили {len(curve)}: {curve}"
        )

    def test_zero_bought_price_excluded_from_curve(self, db):
        """bought_outcome_price=0 не должен вызывать деление на ноль."""
        self._insert_trade(db, bought_price=0.0, pnl_cents=10.0, days_ago=3)
        self._insert_trade(db, bought_price=0.5, pnl_cents=8.0, days_ago=5)

        from web.data_provider import get_equity_curve
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            curve = get_equity_curve(days=30, strategy="whale")

        # Нулевая запись не должна порождать точку или вызывать ZeroDivisionError
        assert len(curve) == 1
        # Значение должно быть конечным числом
        assert all(isinstance(p["daily_pnl"], (int, float)) for p in curve)


# ──────────────────────────────────────────────────────────────
# Баг 3: max_concurrent_chains — нет hard cap, валидация отсутствует
# ──────────────────────────────────────────────────────────────
class TestMaxConcurrentChainsValidation:
    def test_hard_cap_prevents_excessive_chains(self, db):
        """max_concurrent_chains не должен превышать MAX_ALLOWED_CHAINS."""
        # Записываем в БД заведомо большое значение
        db.execute(
            "UPDATE global_settings SET value=? WHERE key=?",
            ("9999", "max_concurrent_chains"),
        )
        db.commit()

        from agents.shared.python.db import get_compound_settings
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            settings = get_compound_settings()

        # Должен быть ограничен hard cap (ожидаем ≤ 20)
        MAX_ALLOWED = 20
        assert settings["max_concurrent_chains"] <= MAX_ALLOWED, (
            f"max_concurrent_chains={settings['max_concurrent_chains']} превышает hard cap {MAX_ALLOWED}"
        )

    def test_negative_chains_defaults_to_one(self, db):
        """Отрицательное или нулевое значение должно нормализоваться в 1."""
        db.execute(
            "UPDATE global_settings SET value=? WHERE key=?",
            ("-5", "max_concurrent_chains"),
        )
        db.commit()

        from agents.shared.python.db import get_compound_settings
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            settings = get_compound_settings()

        assert settings["max_concurrent_chains"] >= 1


# ──────────────────────────────────────────────────────────────
# Баг 4: SQLiteConnectionProxy — get_connection возвращает raw conn,
#         а не proxy, внутри with-блока
# ──────────────────────────────────────────────────────────────
class TestConnectionProxyIntegrity:
    def test_proxy_wraps_context_manager(self):
        """SQLiteConnectionProxy должен сохраняться внутри with-блока."""
        from tests.conftest import SQLiteConnectionProxy

        raw = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(raw)

        with proxy as p:
            # p должен быть тем же proxy, не raw connection
            assert isinstance(p, SQLiteConnectionProxy), (
                "__enter__ должен возвращать SQLiteConnectionProxy, не raw conn"
            )
        raw.close()

    def test_pragma_is_intercepted_by_proxy(self):
        """PRAGMA foreign_keys = ON должен быть заменён на OFF через proxy."""
        from tests.conftest import SQLiteConnectionProxy

        raw = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(raw)

        # Убеждаемся, что PRAGMA перехватывается без исключения
        proxy.execute("PRAGMA foreign_keys = ON")
        result = proxy.execute("PRAGMA foreign_keys").fetchone()
        # После перехвата должно быть OFF (0)
        assert result[0] == 0, f"FK должны быть OFF в тестах, получили: {result[0]}"
        raw.close()

    def test_cursor_proxy_wraps_cursor(self):
        """cursor() должен возвращать SQLiteCursorProxy, не raw cursor."""
        from tests.conftest import SQLiteConnectionProxy, SQLiteCursorProxy

        raw = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(raw)
        cur = proxy.cursor()

        assert isinstance(cur, SQLiteCursorProxy), (
            "proxy.cursor() должен возвращать SQLiteCursorProxy"
        )
        raw.close()
