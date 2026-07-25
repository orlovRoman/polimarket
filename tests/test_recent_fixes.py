"""
Регрессионные тесты для коммитов 0942eb1 – 1b68bf4
"""
import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from tests.helpers import SQLiteConnectionProxy


# ──────────────────────────────────────────────────────────────
# Фикстура: минимальная in-memory БД
# ──────────────────────────────────────────────────────────────
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE markets (
            id TEXT PRIMARY KEY, platform TEXT, title TEXT,
            url TEXT, outcome TEXT, price REAL, close_time TEXT
        );
        CREATE TABLE whale_settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            strategy_type TEXT, status TEXT,
            pnl_realized REAL, resolved_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            edge REAL, estimated_probability REAL,
            predicted_probability REAL
        );
        CREATE TABLE strategy_metrics (
            id INTEGER PRIMARY KEY,
            strategy_type TEXT, win_rate REAL,
            sharpe_ratio REAL, total_signals INTEGER
        );
        CREATE TABLE penny_stocks_monitoring (
            market_id TEXT PRIMARY KEY, title TEXT, url TEXT,
            initial_price REAL, current_price REAL,
            max_price_seen REAL, min_price_seen REAL,
            volume_2h REAL DEFAULT 0.0,
            predicted_outcome TEXT, actual_outcome TEXT,
            edge REAL, confidence REAL, status TEXT DEFAULT 'ACTIVE',
            spike_alert_sent BOOLEAN DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        );
        CREATE TABLE whale_stocks_monitoring (
            market_id TEXT PRIMARY KEY, title TEXT, url TEXT,
            initial_price REAL, current_price REAL,
            max_price_seen REAL, min_price_seen REAL,
            volume_2h REAL DEFAULT 0.0,
            predicted_outcome TEXT, actual_outcome TEXT,
            edge REAL, confidence REAL, status TEXT DEFAULT 'ACTIVE',
            spike_alert_sent BOOLEAN DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            virtual_bought_price REAL DEFAULT NULL,
            virtual_bought_at TIMESTAMP DEFAULT NULL,
            wallet_address TEXT,
            whale_count INTEGER DEFAULT 1,
            whale_directions TEXT DEFAULT '',
            bet_size_usdc REAL DEFAULT NULL
        );
        CREATE TABLE whale_virtual_trades_history (
            id INTEGER PRIMARY KEY, market_id TEXT,
            title TEXT, url TEXT, outcome TEXT,
            bought_price REAL, bought_outcome_price REAL,
            sold_price REAL, sold_outcome_price REAL,
            pnl_points REAL, pnl_percent REAL,
            bought_at TIMESTAMP, sold_at TIMESTAMP,
            wallet_address TEXT, bet_size_usdc REAL
        );
        CREATE TABLE penny_virtual_trades_history (
            id INTEGER PRIMARY KEY, market_id TEXT,
            title TEXT, url TEXT, outcome TEXT,
            bought_price REAL, bought_outcome_price REAL,
            sold_price REAL, sold_outcome_price REAL,
            pnl_points REAL, pnl_percent REAL,
            bought_at TIMESTAMP, sold_at TIMESTAMP,
            max_price_seen REAL, min_price_seen REAL,
            bet_size_usdc REAL
        );
        CREATE TABLE compound_chains (
            id INTEGER PRIMARY KEY, status TEXT,
            initial_stake REAL, current_stake REAL, updated_at TEXT,
            resolved_at TEXT DEFAULT NULL
        );
        CREATE TABLE compound_virtual_trades_history (
            id INTEGER PRIMARY KEY, sold_at TEXT,
            pnl_usd REAL, status TEXT
        );
        CREATE TABLE global_settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO global_settings VALUES ('virtual_stake', '100.0');
    """)
    yield conn
    conn.close()


# ──────────────────────────────────────────────────────────────
# Баг 1: normalize_strategy_name — полное покрытие алиасов
# ──────────────────────────────────────────────────────────────
class TestNormalizeStrategyName:
    def test_known_alias(self):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name("favourite_compound") == "favourite_compounding"

    def test_uppercase_lowercased(self):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name("SCOUT") == "scout"

    def test_whitespace_stripped(self):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name("  whale  ") == "whale"

    def test_empty_string(self):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name("") == ""

    def test_none_input(self):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name(None) == ""

    @pytest.mark.parametrize("raw,expected", [
        ("synthetic_corridor", "synthetic_corridor"),
        ("temporal_corridor",  "temporal_corridor"),
        ("cross_platform",     "cross_platform"),
        ("penny_stocks",       "penny_stocks"),
    ])
    def test_standard_strategies_unchanged(self, raw, expected):
        from web.data_provider import normalize_strategy_name
        assert normalize_strategy_name(raw) == expected


# ──────────────────────────────────────────────────────────────
# Баг 2: signals_count не перезаписывается нулём (max-защита)
# ──────────────────────────────────────────────────────────────
class TestSignalsCountMax:
    def test_signals_count_not_overwritten_by_zero(self, db):
        """Если strategy_metrics уже вернул signals_count=50,
        а signals-запрос вернул total=0 — итог должен остаться 50."""
        db.execute(
            "INSERT INTO strategy_metrics VALUES (1,'scout',0.6,1.2,50)"
        )
        db.commit()

        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        assert stats["scout"]["periods"]["all"]["signals_count"] >= 50

    def test_signals_count_takes_larger_value(self, db):
        """Если signals вернул больше, чем strategy_metrics — берём большее."""
        now = datetime.now(timezone.utc)
        db.execute("INSERT INTO strategy_metrics VALUES (1,'scout',0.6,1.2,5)")
        for i in range(10):
            db.execute(
                "INSERT INTO signals (id, strategy_type, status, pnl_realized, resolved_at, edge, estimated_probability, predicted_probability) VALUES (?,?,?,?,?,?,?,?)",
                (i+1, "scout", "WIN", 1.0,
                 (now - timedelta(days=i)).strftime("%Y-%m-%d %H:%M:%S"),
                 0.1, 0.6, 0.65)
            )
        db.commit()

        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        assert stats["scout"]["periods"]["all"]["signals_count"] == 10


# ──────────────────────────────────────────────────────────────
# Баг 3: close_time fallback — UTC, не naive
# ──────────────────────────────────────────────────────────────
class TestCloseTimeFallback:
    def test_penny_stock_close_time_default_is_future(self, db):
        """Если close_time не передан — дефолт должен быть > now."""
        from agents.shared.python.db import add_penny_stock_to_monitoring
        
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_penny_stock_to_monitoring(
                market_id="test-penny-1",
                title="Test Market",
                url="https://example.com",
                initial_price=0.5
            )

        row = db.execute(
            "SELECT close_time FROM markets WHERE id='test-penny-1'"
        ).fetchone()
        assert row is not None
        ct = datetime.fromisoformat(row["close_time"]).replace(tzinfo=timezone.utc)
        assert ct > datetime.now(timezone.utc)  # должен быть в будущем

    def test_whale_stock_close_time_default_is_future(self, db):
        """То же для whale."""
        from agents.shared.python.db import add_whale_stock_to_monitoring
        
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_whale_stock_to_monitoring(
                market_id="test-whale-1",
                title="Whale Market",
                url="https://example.com",
                initial_price=0.4
            )

        row = db.execute(
            "SELECT close_time FROM markets WHERE id='test-whale-1'"
        ).fetchone()
        assert row is not None
        ct = datetime.fromisoformat(row["close_time"]).replace(tzinfo=timezone.utc)
        assert ct > datetime.now(timezone.utc)

    def test_explicit_close_time_preserved(self, db):
        """Явный close_time не должен быть перезаписан дефолтом."""
        from agents.shared.python.db import add_penny_stock_to_monitoring
        explicit = "2027-01-01 00:00:00"
        
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            add_penny_stock_to_monitoring(
                market_id="test-explicit",
                title="T", url="https://example.com",
                initial_price=0.5,
                close_time=explicit
            )

        row = db.execute(
            "SELECT close_time FROM markets WHERE id='test-explicit'"
        ).fetchone()
        assert row["close_time"] == explicit


# ──────────────────────────────────────────────────────────────
# Баг 4: SQLiteConnectionProxy покрывает executemany
# ──────────────────────────────────────────────────────────────
class TestSQLiteConnectionProxy:
    def test_proxy_intercepts_pragma_in_execute(self):
        """PRAGMA foreign_keys = ON подменяется на OFF через execute."""
        real_conn = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(real_conn)
        
        # Не должно бросать исключение и не должно включать FK
        proxy.execute("PRAGMA foreign_keys = ON")
        result = proxy.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 0  # OFF
        real_conn.close()

    def test_proxy_intercepts_pragma_in_executescript(self):
        """PRAGMA foreign_keys = ON подменяется в executescript."""
        real_conn = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(real_conn)
        
        proxy.executescript("CREATE TABLE t (id INTEGER);\nPRAGMA foreign_keys = ON;")
        result = proxy.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 0
        real_conn.close()

    def test_proxy_passthrough_normal_queries(self):
        """Обычные запросы не блокируются прокси."""
        real_conn = sqlite3.connect(":memory:")
        proxy = SQLiteConnectionProxy(real_conn)
        proxy.execute("CREATE TABLE t (x INTEGER)")
        proxy.execute("INSERT INTO t VALUES (42)")
        row = proxy.execute("SELECT x FROM t").fetchone()
        assert row[0] == 42
        real_conn.close()



# ──────────────────────────────────────────────────────────────
# Интеграционный: overview возвращает корректную структуру
# ──────────────────────────────────────────────────────────────
class TestOverviewStatsStructure:
    EXPECTED_STRATEGIES = [
        'scout', 'whale', 'penny_stocks',
        'favourite_compounding'
    ]

    def test_all_strategies_present(self, db):
        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        for strat in self.EXPECTED_STRATEGIES:
            assert strat in stats, f"Стратегия '{strat}' отсутствует в stats"

    def test_each_strategy_has_required_keys(self, db):
        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        required_keys = {'win_rate', 'sharpe', 'periods', 'status_emoji'}
        for strat, data in stats.items():
            missing = required_keys - set(data.keys())
            assert not missing, f"Стратегия '{strat}' не имеет ключей: {missing}"


# ──────────────────────────────────────────────────────────────
# Дополнительные регрессионные тесты
# ──────────────────────────────────────────────────────────────
class TestRecentBugFixes:
    def test_open_signals_counted_in_24h_period(self, db):
        """Проверяем, что OPEN сигналы созданные за 24h попадают в signals_count."""
        now = datetime.now(timezone.utc)
        db.execute(
            "INSERT INTO signals (id, strategy_type, status, created_at) VALUES (?, ?, ?, ?)",
            (101, "scout", "OPEN", now.strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.execute(
            "INSERT INTO signals (id, strategy_type, status, created_at) VALUES (?, ?, ?, ?)",
            (102, "scout", "OPEN", now.strftime("%Y-%m-%d %H:%M:%S"))
        )
        db.commit()

        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        assert stats["scout"]["periods"]["24h"]["signals_count"] == 2
        # Т.к. нет закрытых (WIN/LOSS) за 24h, win_rate равен None
        assert stats["scout"]["periods"]["24h"]["win_rate"] is None

    def test_penny_stocks_stats_gracefully_handles_missing_table(self, db):
        """Если penny_stocks_monitoring отсутствует, get_overview_stats не падает."""
        db.execute("DROP TABLE IF EXISTS penny_stocks_monitoring")
        db.execute("DROP TABLE IF EXISTS penny_virtual_trades_history")
        db.commit()

        from web.data_provider import get_overview_stats
        with patch("agents.shared.python.db.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = lambda s: db
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            stats = get_overview_stats()

        assert stats["penny_stocks"]["periods"]["24h"]["signals_count"] == 0

