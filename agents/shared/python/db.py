import sqlite3
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from core.models import Market, Signal, MarketCorrelation

# Импортируем путь из единого конфига
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import DB_PATH
import logging

COMPOUND_DEFAULTS = {
    "min_price": "0.95",
    "min_volume": "500",
    "max_hours": "336",
    "virtual_stake": "50",
    "enabled": "1",
    "min_confidence": "0.35"
}

logger = logging.getLogger("NexusPolyBot.DB")

_db_initializing = False

def _ensure_initializing(fn):
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _db_initializing
        _db_initializing = True
        try:
            return fn(*args, **kwargs)
        finally:
            _db_initializing = False
    return wrapper

@contextmanager
def get_connection():
    """Контекст-менеджер для SQLite-соединений с гарантированным закрытием."""
    global _db_initializing
    if not _db_initializing:
        init_db()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _escape_like(pattern: str) -> str:
    """Экранирует спецсимволы для оператора SQL LIKE."""
    return pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

_db_initialized = False
_db_init_lock = threading.Lock()

@_ensure_initializing
def init_db():
    """Инициализация таблиц базы данных. Thread-safe, вызывается один раз."""
    global _db_initialized
    if _db_initialized:
        # Проверяем, действительно ли таблицы существуют в текущем файле БД
        try:
            conn = sqlite3.connect(DB_PATH, timeout=1)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='markets'")
            has_table = cursor.fetchone() is not None
            conn.close()
            if has_table:
                return
        except Exception:
            pass
            
    with _db_init_lock:
        if _db_initialized:  # double-check после получения лока
            try:
                conn = sqlite3.connect(DB_PATH, timeout=1)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='markets'")
                has_table = cursor.fetchone() is not None
                conn.close()
                if has_table:
                    return
            except Exception:
                pass
                
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        with get_connection() as conn:

            cursor = conn.cursor()
            
            # Таблица рынков
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS markets (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    url TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    price REAL NOT NULL,
                    close_time TIMESTAMP NOT NULL,
                    condition_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица китов (ончейн-аналитика)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS known_whales (
                    address TEXT PRIMARY KEY,
                    alias TEXT,
                    win_rate REAL,
                    total_won REAL,
                    total_vol REAL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица аудита идей (Idea Audit)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS idea_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    market_title TEXT,
                    scout_edge REAL,
                    swing_found INTEGER,
                    shadow_agree INTEGER,
                    shadow_confidence REAL,
                    shadow_reason TEXT,
                    final_outcome TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица сигналов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    edge REAL,
                    confidence REAL NOT NULL,
                    priority TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    analysis_report TEXT,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)

            # Таблица: Долгосрочная память (Key-Value)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memory (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL, -- JSON string
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Таблица проанализированных рынков
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_opinions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    opinion TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    agree BOOLEAN NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)
            
            # Таблица проанализированных рынков
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analyzed_markets (
                    market_id TEXT PRIMARY KEY,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_price REAL,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)
            
            # Таблица синтетических коридоров
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS synthetic_corridors (
                    signal_id TEXT PRIMARY KEY,
                    event_slug TEXT,
                    event_title TEXT,
                    event_url TEXT,
                    
                    lower_market_id TEXT,
                    lower_question TEXT,
                    lower_level REAL,
                    lower_level_unit TEXT,
                    lower_price_yes REAL,
                    lower_ask_yes REAL,
                    
                    upper_market_id TEXT,
                    upper_question TEXT,
                    upper_level REAL,
                    upper_level_unit TEXT,
                    upper_price_yes REAL,
                    upper_ask_no REAL,
                    
                    theoretical_cost REAL,
                    theoretical_spread_pct REAL,
                    real_cost REAL,
                    real_spread_pct REAL,
                    
                    executable_contracts REAL,
                    depth_5_lower REAL,
                    depth_5_upper REAL,
                    
                    stake_lower_usd REAL,
                    stake_upper_usd REAL,
                    total_invested_usd REAL,
                    contracts_lower REAL,
                    contracts_upper REAL,
                    pnl_above_upper_usd REAL,
                    pnl_in_corridor_usd REAL,
                    pnl_below_lower_usd REAL,
                    min_guaranteed_usd REAL,
                    roi_min_pct REAL,
                    roi_max_pct REAL,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alerted INTEGER DEFAULT 0
                )
            """)
            
            # Таблица временных коридоров
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS temporal_corridors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT UNIQUE,
                    event_slug TEXT,
                    event_title TEXT,
                    event_url TEXT,
                    
                    early_market_id TEXT, early_question TEXT, early_expiry TEXT, early_cost REAL,
                    late_market_id TEXT, late_question TEXT, late_expiry TEXT, late_cost REAL,
                    
                    date_gap_days INTEGER,
                    theoretical_cost REAL, theoretical_spread_pct REAL,
                    real_cost REAL, real_spread_pct REAL,
                    
                    early_stake_usd REAL, late_stake_usd REAL,
                    early_contracts REAL, late_contracts REAL,
                    ev_usd REAL, roi_pct REAL,
                    
                    quality_score REAL,
                    exit_rule TEXT,
                    
                    status TEXT DEFAULT 'ACTIVE',
                    created_at TIMESTAMP,
                    alerted INTEGER DEFAULT 0
                )
            """)
            
            # Таблица отправленных уведомлений (дедупликация)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sent_alerts (
                    alert_key TEXT PRIMARY KEY,
                    alert_type TEXT NOT NULL,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица: Профили кошельков (Smart Money Tracker)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wallets (
                    address TEXT PRIMARY KEY,
                    alias TEXT,
                    win_rate REAL DEFAULT 0.0,
                    total_profit REAL DEFAULT 0.0,
                    is_insider BOOLEAN DEFAULT FALSE,
                    last_seen DATETIME,
                    notes TEXT
                )
            ''')

            # Таблица: Крупные сделки трейдеров (Smart Money Bets)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trader_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    price REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (wallet_address) REFERENCES wallets (address),
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            ''')
            
            # Таблица истории чата Telegram
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица входящих постов Telegram (для event-driven анализа)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telegram_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'NEW',
                    UNIQUE(chat_id, message_id)
                )
            """)
            
            # 1. ── Сначала ВСЕ DROP старых триггеров и таблиц ──
            cursor.execute("DROP TRIGGER IF EXISTS agent_episodes_ai")
            cursor.execute("DROP TRIGGER IF EXISTS agent_episodes_ad")
            cursor.execute("DROP TRIGGER IF EXISTS agent_episodes_au")
            cursor.execute("DROP TABLE IF EXISTS agent_episodes_fts")

            # 2. ── Таблица agent_episodes (должна быть ДО FTS и триггеров) ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    market_id TEXT,
                    market_title TEXT,
                    summary TEXT NOT NULL,
                    context TEXT,
                    outcome TEXT DEFAULT 'unknown',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)

            # 3. ── FTS5 виртуальная таблица (ПОСЛЕ agent_episodes) ──
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_episodes_fts USING fts5(
                    episode_id UNINDEXED,
                    agent_name,
                    summary,
                    context
                )
            """)
            
            # 4. ── Триггеры (ПОСЛЕ обеих таблиц) ──
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS agent_episodes_ai_v2 AFTER INSERT ON agent_episodes BEGIN
                    INSERT INTO agent_episodes_fts(episode_id, agent_name, summary, context) 
                    VALUES (new.id, new.agent_name, new.summary, COALESCE(new.context, '{}'));
                END;
            """)
            
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS agent_episodes_ad_v2 AFTER DELETE ON agent_episodes BEGIN
                    DELETE FROM agent_episodes_fts WHERE episode_id = old.id;
                END;
            """)
            
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS agent_episodes_au_v2 AFTER UPDATE ON agent_episodes BEGIN
                    DELETE FROM agent_episodes_fts WHERE episode_id = old.id;
                    INSERT INTO agent_episodes_fts(episode_id, agent_name, summary, context) 
                    VALUES (new.id, new.agent_name, new.summary, COALESCE(new.context, '{}'));
                END;
            """)
            
            # Таблица истории цен (для трендового анализа)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    price REAL NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)

            # Таблица корреляций между рынками
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS correlations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id_a TEXT NOT NULL,
                    market_id_b TEXT NOT NULL,
                    title_a TEXT,
                    title_b TEXT,
                    correlation_type TEXT NOT NULL,
                    description TEXT,
                    confidence REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notified BOOLEAN DEFAULT FALSE
                )
            """)

            # Новые таблицы для гейта и кластеризации
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wallet_clusters (
                    cluster_id   TEXT NOT NULL,
                    address      TEXT NOT NULL,
                    funding_addr TEXT NOT NULL,
                    first_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (cluster_id, address)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_clusters_address ON wallet_clusters(address)"
            )

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gate_metrics (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      TEXT NOT NULL,
                    total       INTEGER NOT NULL,
                    passed      INTEGER NOT NULL,
                    blocked_no_volume  INTEGER NOT NULL DEFAULT 0,
                    blocked_no_whales  INTEGER NOT NULL DEFAULT 0,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Индексы для ускорения частых запросов
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)")
            
            # Разрешаем конфликт дубликатов PENDING сигналов перед созданием уникального индекса
            cursor.execute("""
                UPDATE signals 
                SET status = 'PENDING' 
                WHERE UPPER(TRIM(status)) = 'PENDING'
            """)
            cursor.execute("""
                UPDATE signals 
                SET status = 'ARCHIVED' 
                WHERE status = 'PENDING' 
                  AND id NOT IN (
                      SELECT id 
                      FROM signals s1
                      WHERE status = 'PENDING'
                        AND id = (
                            SELECT id 
                            FROM signals 
                            WHERE market_id = s1.market_id 
                              AND status = 'PENDING' 
                            ORDER BY created_at DESC, id DESC 
                            LIMIT 1
                        )
                  )
            """)
            
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_market_pending ON signals(market_id) WHERE status = 'PENDING'")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_opinions_market ON agent_opinions(market_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_chat ON chat_history(chat_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_close ON markets(close_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_market ON price_history(market_id, recorded_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_correlations_new ON correlations(notified, detected_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trader_transactions_market ON trader_transactions(market_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_agent ON agent_episodes(agent_name, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON agent_episodes(outcome, event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_idea_audit_created_at ON idea_audit (created_at)")

            # Миграция: добавляем новые колонки в memory (если их ещё нет)
            existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(memory)").fetchall()}
            if 'category' not in existing_cols:
                cursor.execute("ALTER TABLE memory ADD COLUMN category TEXT DEFAULT 'general'")
            if 'ttl' not in existing_cols:
                cursor.execute("ALTER TABLE memory ADD COLUMN ttl INTEGER DEFAULT NULL")
            if 'priority' not in existing_cols:
                cursor.execute("ALTER TABLE memory ADD COLUMN priority INTEGER DEFAULT 0")
            if 'expires_at' not in existing_cols:
                cursor.execute("ALTER TABLE memory ADD COLUMN expires_at DATETIME DEFAULT NULL")

            # Индекс по категории (создается после миграции, так как колонка category может отсутствовать при чистом создании)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_category ON memory (category)")

            # Миграция: добавляем новые колонки в markets (tokens, volume, condition_id)
            market_cols = {row[1] for row in cursor.execute("PRAGMA table_info(markets)").fetchall()}
            if 'tokens' not in market_cols:
                cursor.execute("ALTER TABLE markets ADD COLUMN tokens TEXT DEFAULT NULL")
            if 'volume' not in market_cols:
                cursor.execute("ALTER TABLE markets ADD COLUMN volume REAL DEFAULT NULL")
            if 'condition_id' not in market_cols:
                cursor.execute("ALTER TABLE markets ADD COLUMN condition_id TEXT DEFAULT NULL")
            
            # Таблица логов LLM
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    market_id TEXT,
                    prompt TEXT,
                    response TEXT,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    latency_ms INTEGER,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_agent_date ON llm_calls(agent_name, created_at)")

            # Таблица: Индекс vault (Layer 1 ↔ Layer 2/3 связь)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vault_index (
                    path TEXT PRIMARY KEY,
                    category TEXT,
                    title TEXT,
                    tags TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    content_hash TEXT
                )
            """)
            # Удален token_usage

            # Таблица: Кросс-платформенные арбитражные сигналы (Polymarket ↔ Kalshi и др.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cross_arbitrage_signals (
                    id TEXT PRIMARY KEY,
                    market_a_id TEXT NOT NULL,
                    market_a_platform TEXT NOT NULL,
                    market_a_title TEXT NOT NULL,
                    market_a_price REAL NOT NULL,
                    market_a_url TEXT NOT NULL,
                    market_b_id TEXT NOT NULL,
                    market_b_platform TEXT NOT NULL,
                    market_b_title TEXT NOT NULL,
                    market_b_price REAL NOT NULL,
                    market_b_url TEXT NOT NULL,
                    has_arbitrage INTEGER NOT NULL,
                    arbitrage_type TEXT NOT NULL,
                    spread_percent REAL NOT NULL,
                    reasoning TEXT,
                    trade_instruction TEXT,
                    match_score REAL NOT NULL,
                    status TEXT DEFAULT 'new',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cross_arb_status "
                "ON cross_arbitrage_signals(status, created_at)"
            )

            # Миграция: target_outcome и estimated_probability в signals
            signal_cols = {row[1] for row in cursor.execute("PRAGMA table_info(signals)").fetchall()}
            if 'target_outcome' not in signal_cols:
                cursor.execute("ALTER TABLE signals ADD COLUMN target_outcome TEXT DEFAULT 'YES'")
            if 'estimated_probability' not in signal_cols:
                cursor.execute("ALTER TABLE signals ADD COLUMN estimated_probability REAL DEFAULT NULL")
                
            # Миграция: prompt_version и had_performance_ctx в llm_calls
            llm_cols = {row[1] for row in cursor.execute("PRAGMA table_info(llm_calls)").fetchall()}
            if 'prompt_version' not in llm_cols:
                cursor.execute("ALTER TABLE llm_calls ADD COLUMN prompt_version TEXT DEFAULT 'v1'")
            if 'had_performance_ctx' not in llm_cols:
                cursor.execute("ALTER TABLE llm_calls ADD COLUMN had_performance_ctx INTEGER DEFAULT 0")

            # Миграция: новые поля в cross_arbitrage_signals
            cross_arb_cols = {row[1] for row in cursor.execute("PRAGMA table_info(cross_arbitrage_signals)").fetchall()}
            for col, default in [
                ("action_a", "'SKIP'"), ("action_b", "'SKIP'"),
                ("entry_price_a_cents", "NULL"), ("entry_price_b_cents", "NULL"),
                ("expected_pnl_pct", "NULL"), ("risk_level", "'MEDIUM'"),
            ]:
                if col not in cross_arb_cols:
                    cursor.execute(f"ALTER TABLE cross_arbitrage_signals ADD COLUMN {col} TEXT DEFAULT {default}")

            # Таблица списков рынков (Игнорировать / Следить)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_lists (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id   TEXT NOT NULL,
                    market_title TEXT,
                    list_type   TEXT NOT NULL CHECK(list_type IN ('ignored', 'watching')),
                    base_price  REAL DEFAULT NULL,
                    last_price  REAL DEFAULT NULL,
                    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(market_id, list_type)
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_lists_type ON market_lists(list_type)"
            )

            # Миграция Evaluation Engine: новые колонки в signals
            signal_cols = {row[1] for row in cursor.execute("PRAGMA table_info(signals)").fetchall()}
            eval_cols = [
                ("predicted_probability", "REAL"),
                ("market_price_at_signal", "REAL"),
                ("edge_at_signal", "REAL"),
                ("strategy_type", "TEXT"),
                ("resolved_at", "TIMESTAMP"),
                ("resolution_outcome", "TEXT"),
                ("resolution_price", "REAL"),
                ("was_profitable", "INTEGER"),
                ("pnl_realized", "REAL"),
                ("analysis_report", "TEXT")
            ]
            for col, col_type in eval_cols:
                if col not in signal_cols:
                    cursor.execute(f"ALTER TABLE signals ADD COLUMN {col} {col_type} DEFAULT NULL")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_resolved_at ON signals(resolved_at)")

            # Таблица strategy_metrics
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_type TEXT NOT NULL,
                    period_start TIMESTAMP NOT NULL,
                    period_end TIMESTAMP NOT NULL,
                    total_signals INTEGER NOT NULL,
                    resolved_signals INTEGER NOT NULL,
                    profitable_signals INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    avg_edge REAL,
                    avg_realized_pnl REAL,
                    brier_score REAL,
                    calibration_error REAL,
                    sharpe_ratio REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_metrics_unique 
                ON strategy_metrics (strategy_type, period_start, period_end)
            """)

            # Миграция: добавляем sharpe_ratio в strategy_metrics (если его нет)
            metrics_cols = {row[1] for row in cursor.execute("PRAGMA table_info(strategy_metrics)").fetchall()}
            if 'sharpe_ratio' not in metrics_cols:
                cursor.execute("ALTER TABLE strategy_metrics ADD COLUMN sharpe_ratio REAL DEFAULT NULL")

            # Таблица calibration_params
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration_params (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_type TEXT NOT NULL,
                    param_name TEXT NOT NULL,
                    param_value REAL NOT NULL,
                    previous_value REAL NOT NULL,
                    reason TEXT NOT NULL,
                    auto_applied INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Миграция wallets: добавляем поля для p-value фильтра
            wallet_cols = {row[1] for row in cursor.execute("PRAGMA table_info(wallets)").fetchall()}
            for col, col_type, default in [
                ("n_trades", "INTEGER", "0"),
                ("n_wins",   "INTEGER", "0"),
                ("p_value",  "REAL",    "1.0"),
            ]:
                if col not in wallet_cols:
                    cursor.execute(
                        f"ALTER TABLE wallets ADD COLUMN {col} {col_type} DEFAULT {default}"
                    )

            # Таблица мониторинга Penny Stocks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS penny_stocks_monitoring (
                    market_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    initial_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    max_price_seen REAL NOT NULL,
                    min_price_seen REAL NOT NULL,
                    volume_2h REAL NOT NULL DEFAULT 0.0,
                    predicted_outcome TEXT,
                    actual_outcome TEXT,
                    edge REAL,
                    confidence REAL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    spike_alert_sent BOOLEAN DEFAULT 0,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_penny_status ON penny_stocks_monitoring(status)")

            # Таблица истории виртуальных сделок
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS penny_virtual_trades_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    bought_price REAL NOT NULL,
                    bought_outcome_price REAL NOT NULL,
                    sold_price REAL NOT NULL,
                    sold_outcome_price REAL NOT NULL,
                    pnl_cents REAL NOT NULL,
                    pnl_percent REAL NOT NULL,
                    bought_at TIMESTAMP,
                    sold_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (market_id) REFERENCES markets (id)
                )
            """)

            # Миграция: добавляем поля виртуального портфеля в penny_stocks_monitoring
            penny_cols = {row[1] for row in cursor.execute("PRAGMA table_info(penny_stocks_monitoring)").fetchall()}
            if 'virtual_bought_price' not in penny_cols:
                cursor.execute("ALTER TABLE penny_stocks_monitoring ADD COLUMN virtual_bought_price REAL DEFAULT NULL")
            if 'virtual_bought_at' not in penny_cols:
                cursor.execute("ALTER TABLE penny_stocks_monitoring ADD COLUMN virtual_bought_at TIMESTAMP DEFAULT NULL")

            # Миграция: удаление некорректных legacy записей из мониторинга
            cursor.execute("""
                DELETE FROM penny_stocks_monitoring 
                WHERE (predicted_outcome = 'NO' AND initial_price <= 0.10)
                   OR (predicted_outcome = 'YES' AND initial_price >= 0.90)
            """)

            # Таблица Favourite Compounding возможностей
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS compound_opportunities (
                    id          TEXT PRIMARY KEY,        -- market_id + '_' + detected_at[:10]
                    market_id   TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    url         TEXT NOT NULL,
                    price       REAL NOT NULL,           -- текущая цена YES (≥0.95)
                    volume_usd  REAL NOT NULL,
                    close_time  TEXT NOT NULL,
                    hours_left  REAL NOT NULL,
                    spread_pct  REAL,                    -- (ask - bid) / mid
                    roi_net_pct REAL,                    -- (1 - price) / price * 100
                    confidence  REAL NOT NULL,           -- 0.0–1.0 от валидатора
                    obviousness_reason TEXT,             -- текст от Google Grounding
                    status      TEXT DEFAULT 'NEW',      -- NEW | ALERTED | BOUGHT | RESOLVED | EXPIRED
                    alerted_at  TEXT,
                    resolved_at TEXT,
                    actual_outcome TEXT,
                    exit_price  REAL,
                    pnl_usd     REAL,
                    outcome     TEXT DEFAULT 'YES',
                    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_compound_status ON compound_opportunities(status, close_time)"
            )

            # Автоматическая миграция: добавляем outcome в compound_opportunities
            cursor.execute("PRAGMA table_info(compound_opportunities)")
            cols = [col[1] for col in cursor.fetchall()]
            if "outcome" not in cols:
                cursor.execute("ALTER TABLE compound_opportunities ADD COLUMN outcome TEXT DEFAULT 'YES'")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS compound_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # Дефолтные настройки
            for k, v in COMPOUND_DEFAULTS.items():
                cursor.execute("INSERT OR IGNORE INTO compound_settings (key, value) VALUES (?, ?)", (k, v))
            
            # Миграция: обновляем старые дефолты до новых
            cursor.execute("UPDATE compound_settings SET value = '500' WHERE key = 'min_volume' AND value = '1000'")
            cursor.execute("UPDATE compound_settings SET value = '336' WHERE key = 'max_hours' AND value = '48'")
            cursor.execute("UPDATE compound_settings SET value = '0.35' WHERE key = 'min_confidence' AND value = '0.5'")
            cursor.execute("UPDATE compound_settings SET value = '500' WHERE key = 'min_volume' AND value = '10000'")

            # Таблица черного списка тегов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_tags (
                    tag TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        _db_initialized = True
        logger.info(f"База данных инициализирована по адресу: {DB_PATH}")

# ─── Списки рынков: Игнорировать / Следить ──────────────────────────────────

def add_to_market_list(market_id: str, market_title: str, list_type: str, base_price: float = None) -> None:
    """Добавляет рынок в список 'ignored' или 'watching'. Идемпотентен (INSERT OR REPLACE)."""
    assert list_type in ('ignored', 'watching'), f"Неизвестный list_type: {list_type}"
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO market_lists
               (market_id, market_title, list_type, base_price, last_price, added_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (market_id, market_title, list_type, base_price, base_price)
        )
    logger.info(f"[MarketLists] Рынок {market_id!r} добавлен в '{list_type}'.")


def remove_from_market_list(market_id: str, list_type: str = None) -> int:
    """Удаляет рынок из списка. Если list_type=None — удаляет из обоих. Возвращает кол-во удалённых строк."""
    use_like = len(market_id) < 36
    if use_like:
        query_id = f"{_escape_like(market_id)}%"
        op = "LIKE ?"
        escape_clause = "ESCAPE '\\'"
    else:
        query_id = market_id
        op = "= ?"
        escape_clause = ""

    with get_connection() as conn:
        if list_type:
            cursor = conn.execute(
                f"DELETE FROM market_lists WHERE market_id {op} {escape_clause} AND list_type = ?",
                (query_id, list_type)
            )
        else:
            cursor = conn.execute(
                f"DELETE FROM market_lists WHERE market_id {op} {escape_clause}",
                (query_id,)
            )
        rows = cursor.rowcount
    logger.info(f"[MarketLists] Рынок {market_id!r} удалён из '{list_type or 'все'}' ({rows} строк).")
    return rows


def is_in_market_list(market_id: str, list_type: str) -> bool:
    """Возвращает True, если рынок находится в указанном списке."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM market_lists WHERE market_id = ? AND list_type = ?",
            (market_id, list_type)
        ).fetchone()
    return row is not None


def get_all_listed_market_ids() -> dict[str, set]:
    """Возвращает {'ignored': {id1, id2...}, 'watching': {id3...}} одним запросом."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT market_id, list_type FROM market_lists"
        ).fetchall()
    result = {'ignored': set(), 'watching': set()}
    for row in rows:
        lt = row['list_type']
        if lt in result:
            result[lt].add(row['market_id'])
    return result


def get_market_list(list_type: str) -> List[dict]:
    """Возвращает все рынки из указанного списка в виде list[dict]."""
    assert list_type in ('ignored', 'watching'), f"Неизвестный list_type: {list_type}"
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT market_id, market_title, list_type, base_price, last_price, added_at
               FROM market_lists WHERE list_type = ? ORDER BY added_at DESC""",
            (list_type,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_watchlist_price(market_id: str, last_price: float) -> None:
    """Обновляет last_price для рынка в watchlist. base_price не трогает."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE market_lists SET last_price = ? WHERE market_id = ? AND list_type = 'watching'",
            (last_price, market_id)
        )


def save_cross_arbitrage(signal) -> None:
    """Сохраняет или обновляет кросс-платформенный арбитражный сигнал."""
    init_db()
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO cross_arbitrage_signals
            (id, market_a_id, market_a_platform, market_a_title, market_a_price, market_a_url,
             market_b_id, market_b_platform, market_b_title, market_b_price, market_b_url,
             has_arbitrage, arbitrage_type, spread_percent, reasoning, trade_instruction,
             match_score, status, action_a, action_b, entry_price_a_cents, entry_price_b_cents,
             expected_pnl_pct, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"{signal.market_a_id}__{signal.market_b_id}",
            signal.market_a_id, signal.market_a_platform, signal.market_a_title,
            signal.market_a_price, signal.market_a_url,
            signal.market_b_id, signal.market_b_platform, signal.market_b_title,
            signal.market_b_price, signal.market_b_url,
            int(signal.has_arbitrage), signal.arbitrage_type, signal.spread_percent,
            signal.reasoning, signal.trade_instruction,
            signal.match_score, signal.status,
            signal.action_a, signal.action_b,
            signal.entry_price_a_cents, signal.entry_price_b_cents,
            signal.expected_pnl_pct, signal.risk_level,
        ))


def get_new_cross_arbitrage_signals(min_spread: float = 5.0) -> list:
    """Возвращает новые алерты с достаточным спредом для отправки в Telegram."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM cross_arbitrage_signals
            WHERE status = 'new' AND has_arbitrage = 1 AND spread_percent >= ?
            ORDER BY spread_percent DESC
        """, (min_spread,))
        return [dict(row) for row in cursor.fetchall()]


def mark_cross_arbitrage_alerted(signal_id: str) -> None:
    """Помечает сигнал как отправленный в Telegram."""
    init_db()
    with get_connection() as conn:
        conn.execute(
            "UPDATE cross_arbitrage_signals SET status = 'alerted' WHERE id = ?",
            (signal_id,)
        )

def save_synthetic_corridor(signal) -> None:
    """Сохраняет сигнал синтетического коридора."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO synthetic_corridors (
                signal_id, event_slug, event_title, event_url,
                lower_market_id, lower_question, lower_level, lower_level_unit, lower_price_yes, lower_ask_yes,
                upper_market_id, upper_question, upper_level, upper_level_unit, upper_price_yes, upper_ask_no,
                theoretical_cost, theoretical_spread_pct, real_cost, real_spread_pct,
                executable_contracts, depth_5_lower, depth_5_upper,
                stake_lower_usd, stake_upper_usd, total_invested_usd, contracts_lower, contracts_upper,
                pnl_above_upper_usd, pnl_in_corridor_usd, pnl_below_lower_usd,
                min_guaranteed_usd, roi_min_pct, roi_max_pct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.signal_id, signal.event_slug, signal.event_title, signal.event_url,
            signal.lower_market_id, signal.lower_question, signal.lower_level, signal.lower_level_unit, signal.lower_price_yes, signal.lower_ask_yes,
            signal.upper_market_id, signal.upper_question, signal.upper_level, signal.upper_level_unit, signal.upper_price_yes, signal.upper_ask_no,
            signal.theoretical_cost, signal.theoretical_spread_pct, signal.real_cost, signal.real_spread_pct,
            signal.executable_contracts, signal.depth_5_lower, signal.depth_5_upper,
            signal.stake_lower_usd, signal.stake_upper_usd, signal.total_invested_usd, signal.contracts_lower, signal.contracts_upper,
            signal.pnl_above_upper_usd, signal.pnl_in_corridor_usd, signal.pnl_below_lower_usd,
            signal.min_guaranteed_usd, signal.roi_min_pct, signal.roi_max_pct, signal.created_at
        ))

def mark_synthetic_corridor_alerted(signal_id: str):
    with get_connection() as conn:
        conn.execute("UPDATE synthetic_corridors SET alerted = 1 WHERE signal_id = ?", (signal_id,))

def get_unalerted_synthetic_corridors() -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM synthetic_corridors WHERE alerted = 0")
        return [dict(r) for r in cursor.fetchall()]

def save_temporal_corridor(signal) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO temporal_corridors (
                signal_id, event_slug, event_title, event_url,
                early_market_id, early_question, early_expiry, early_cost,
                late_market_id, late_question, late_expiry, late_cost,
                date_gap_days, theoretical_cost, theoretical_spread_pct,
                real_cost, real_spread_pct, early_stake_usd, late_stake_usd,
                early_contracts, late_contracts, ev_usd, roi_pct,
                quality_score, exit_rule, status, created_at, alerted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', CURRENT_TIMESTAMP, 0)
        """, (
            signal.signal_id, signal.event_slug, signal.event_title, signal.event_url,
            signal.early_market_id, signal.early_question, signal.early_expiry.isoformat(), signal.early_cost,
            signal.late_market_id, signal.late_question, signal.late_expiry.isoformat(), signal.late_cost,
            signal.date_gap_days, signal.theoretical_cost, signal.theoretical_spread_pct,
            signal.real_cost, signal.real_spread_pct, signal.early_stake_usd, signal.late_stake_usd,
            signal.early_contracts, signal.late_contracts, signal.ev_usd, signal.roi_pct,
            signal.quality_score, signal.exit_rule
        ))

def get_unalerted_temporal_corridors() -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM temporal_corridors WHERE alerted = 0")
        return [dict(r) for r in cursor.fetchall()]

def mark_temporal_corridor_alerted(signal_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE temporal_corridors SET alerted = 1 WHERE signal_id = ?", (signal_id,))

def is_alert_already_sent(alert_key: str, ttl_hours: int = 12) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sent_at FROM sent_alerts WHERE alert_key = ?",
            (alert_key,)
        )
        row = cursor.fetchone()
        if not row:
            return False
        sent_at_str = str(row["sent_at"]).replace(" ", "T").replace("Z", "+00:00")
        sent_at = datetime.fromisoformat(sent_at_str)
        # Учитываем, что sent_at сохраняется в UTC
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - sent_at < timedelta(hours=ttl_hours):
            return True
        return False

def mark_alert_sent(alert_key: str, alert_type: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO sent_alerts (alert_key, alert_type, sent_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (alert_key, alert_type)
        )

def save_idea_audit(market_id: str, market_title: str, audit_data: dict):
    """Сохраняет аудит-запись о прохождении идеи через pipeline."""
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO idea_audit 
                (market_id, market_title, scout_edge, swing_found, shadow_agree, shadow_confidence,
                 shadow_reason, final_outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, market_title,
                audit_data.get("scout_edge"),
                audit_data.get("swing_found", 0),
                audit_data.get("shadow_agree"),
                audit_data.get("shadow_confidence"),
                audit_data.get("shadow_reason", ""),
                audit_data.get("final_outcome", "unknown")
            ))
    except Exception as e:
        logger.error(f"[DB] Ошибка при сохранении idea_audit для рынка '{market_id}': {e}")


def get_token_usage_last_24h(agent_name: str) -> dict:
    from core.logger import LLMLogger
    return LLMLogger.get_token_usage_last_24h(agent_name)

def get_detailed_token_usage_last_24h(agent_name: str) -> list:
    from core.logger import LLMLogger
    return LLMLogger.get_detailed_token_usage_last_24h(agent_name)

def get_agent_model(agent_name: str, default_model: str = "gemini-2.5-flash") -> str:
    """Возвращает последнюю использованную модель для агента из логов токенов."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT model_name FROM llm_calls WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1",
                (agent_name,)
            )
            row = cursor.fetchone()
            if row and row['model_name']:
                return row['model_name']
    except Exception as e:
        logger.error(f"[DB] Ошибка получения последней модели агента: {e}")
    return default_model

def save_chat_message(chat_id: int, role: str, content: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (chat_id, role, content)
            VALUES (?, ?, ?)
        """, (chat_id, role, content))

def get_chat_history(chat_id: int, limit: int = 20):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM chat_history
            WHERE chat_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (chat_id, limit))
        rows = cursor.fetchall()
        # Возвращаем в хронологическом порядке
        history = [{"role": row["role"], "parts": [{"text": row["content"]}]} for row in reversed(rows)]
        
        # Убеждаемся, что история начинается с сообщения пользователя (требование Gemini API)
        if history and history[0]["role"] != "user":
            history = history[1:]
            
        return history

def cleanup_chat_history(chat_id: int, keep_last: int = 20):
    """Обёртка для обратной совместимости."""
    compress_and_cleanup_chat_history(chat_id, keep_last=keep_last)

def compress_and_cleanup_chat_history(chat_id: int, keep_last: int = 20, summarize_threshold: int = 40):
    """
    Если сообщений > summarize_threshold — архивирует старые в memory
    и сохраняет summary как факт перед удалением.
    Иначе — просто обрезает до keep_last.
    """
    summary_to_save = None

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chat_history WHERE chat_id = ?", (chat_id,))
        count = cursor.fetchone()[0]

        if count > summarize_threshold:
            cursor.execute("""
                SELECT role, content FROM chat_history
                WHERE chat_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (chat_id, count - keep_last))
            old_msgs = [f"{r['role']}: {r['content'][:200]}" for r in cursor.fetchall()]
            if old_msgs:
                summary_to_save = (
                    f"[Архив диалога от {datetime.now(timezone.utc).strftime('%Y-%m-%d')}]: "
                    + " | ".join(old_msgs[:100])
                )

        cursor.execute("""
            DELETE FROM chat_history
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM chat_history
                WHERE chat_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
        """, (chat_id, chat_id, keep_last))

    if summary_to_save:
        save_memory(
            key=f"chat_archive_{chat_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            value=summary_to_save,
            category='episodic',
            priority=5,
            ttl=30 * 24 * 3600
        )


def get_db_stats():
    """Получает статистику из БД"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM markets")
        m_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM signals")
        s_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM agent_opinions")
        o_count = cursor.fetchone()[0]
    
    return f"📊 <b>Статистика БД:</b>\n- Рынков: {m_count}\n- Сигналов: {s_count}\n- Мнений: {o_count}"

def get_signals(limit: int = 5):
    """Получает последние сигналы со статусом PENDING из БД"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, m.title, m.url, m.price as market_price 
            FROM signals s 
            JOIN markets m ON s.market_id = m.id 
            WHERE s.status = 'PENDING'
            ORDER BY s.created_at DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def archive_signal_by_id(signal_id: str) -> bool:
    """Устанавливает статус 'ARCHIVED' для сигнала по его ID (или по началу ID, если передан усечённый).
    Возвращает True, если был изменен хотя бы один сигнал."""
    use_like = len(signal_id) < 36
    if use_like:
        query_id = f"{_escape_like(signal_id)}%"
        op = "LIKE ? ESCAPE '\\'"
    else:
        query_id = signal_id
        op = "= ?"
    with get_connection() as conn:
        cursor = conn.execute(
            f"UPDATE signals SET status = 'ARCHIVED' WHERE id {op}",
            (query_id,)
        )
        return cursor.rowcount > 0

def update_signal_analysis_report(signal_id: str, report: str) -> bool:
    """Сохраняет HTML-отчет консенсуса агентов для сигнала в memory по ключу consensus_report_{signal_id}."""
    save_memory(f"consensus_report_{signal_id}", report, category="cache")
    return True

def get_signal_analysis_report(signal_id: str) -> Optional[str]:
    """
    Возвращает сохранённый HTML-отчет консенсуса агентов для сигнала.
    Ищет в memory по ключу consensus_report_{signal_id} (поддерживает усеченный ID).
    """
    use_like = len(signal_id) < 36
    with get_connection() as conn:
        if use_like:
            row = conn.execute(
                r"SELECT value FROM memory WHERE key LIKE ? ESCAPE '\' LIMIT 1",
                (f"consensus_report_{_escape_like(signal_id)}%",)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT value FROM memory WHERE key = ? LIMIT 1",
                (f"consensus_report_{signal_id}",)
            ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]

def get_signal_by_id(signal_id: str) -> Optional[dict]:
    """Возвращает сигнал по полному или усечённому ID."""
    use_like = len(signal_id) < 36
    op = f"LIKE ? ESCAPE '\\'" if use_like else "= ?"
    query_id = f"{_escape_like(signal_id)}%" if use_like else signal_id
    with get_connection() as conn:
        row = conn.execute(
            f"""SELECT s.*, m.title, m.url, m.price as market_price 
                FROM signals s 
                JOIN markets m ON s.market_id = m.id 
                WHERE s.id {op} LIMIT 1""",
            (query_id,)
        ).fetchone()
    return dict(row) if row else None

def save_market(market: Market):
    with get_connection() as conn:
        cursor = conn.cursor()
        tokens_json = json.dumps(market.tokens) if market.tokens else None
        cursor.execute("""
            INSERT OR REPLACE INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume, condition_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (market.id, market.platform, market.title, market.description, market.url, market.outcome, market.price, market.close_time.isoformat(), tokens_json, market.volume, market.condition_id))

def get_market_from_db(market_id: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM markets WHERE id = ?", (market_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("tokens"):
            try:
                res["tokens"] = json.loads(res["tokens"])
            except Exception:
                pass
        return res


def save_signal(signal: Signal, details_obj=None, or_ignore: bool = False) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        details_str = details_obj.model_dump_json() if details_obj else signal.details
        
        target_outcome = details_obj.target_outcome if details_obj else 'YES'
        estimated_prob = details_obj.estimated_probability if details_obj else signal.confidence
        
        if or_ignore:
            cursor.execute("""
                INSERT OR IGNORE INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, target_outcome, estimated_probability)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal.id, signal.type, signal.market_id, signal.platform, signal.edge, signal.confidence, signal.priority, signal.summary, details_str, getattr(signal, 'status', 'PENDING'), signal.created_at.isoformat(), target_outcome, estimated_prob))
        else:
            cursor.execute("""
                INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, target_outcome, estimated_probability)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type=excluded.type,
                    market_id=excluded.market_id,
                    platform=excluded.platform,
                    edge=excluded.edge,
                    confidence=excluded.confidence,
                    priority=excluded.priority,
                    summary=excluded.summary,
                    details=excluded.details,
                    status=excluded.status,
                    target_outcome=excluded.target_outcome,
                    estimated_probability=excluded.estimated_probability
            """, (signal.id, signal.type, signal.market_id, signal.platform, signal.edge, signal.confidence, signal.priority, signal.summary, details_str, getattr(signal, 'status', 'PENDING'), signal.created_at.isoformat(), target_outcome, estimated_prob))
        return cursor.rowcount > 0

def save_arbitrage_signal_to_db(arb_signal) -> bool:
    """Сохраняет арбитраж в таблицу cross_arbitrage_signals, не в signals."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO cross_arbitrage_signals
            (id, market_a_id, market_a_platform, market_a_title, market_a_price, market_a_url,
             market_b_id, market_b_platform, market_b_title, market_b_price, market_b_url,
             has_arbitrage, arbitrage_type, spread_percent, reasoning, trade_instruction,
             match_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            arb_signal.id,
            arb_signal.market_id_a, arb_signal.platform_a, arb_signal.summary, arb_signal.edge, f"https://polymarket.com/event/{arb_signal.market_id_a}",
            arb_signal.market_id_b or "", arb_signal.platform_b or "", arb_signal.summary, arb_signal.edge, f"https://kalshi.com/event/{arb_signal.market_id_b}" if arb_signal.market_id_b else "",
            1, arb_signal.type, arb_signal.spread_pct,
            arb_signal.details, arb_signal.details,
            arb_signal.confidence, arb_signal.status
        ))
    return True

def mark_market_analyzed(market_id: str, price: float):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO analyzed_markets (market_id, last_price, analyzed_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (market_id, price))

def get_last_analyzed_price(market_id: str) -> Optional[float]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_price FROM analyzed_markets WHERE market_id = ?", (market_id,))
        row = cursor.fetchone()
        return row['last_price'] if row else None

def get_last_analyzed_prices(market_ids: set) -> dict[str, float]:
    """Возвращает {market_id: last_price} одним запросом для всех ID."""
    if not market_ids:
        return {}
    placeholders = ','.join('?' * len(market_ids))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT market_id, last_price FROM analyzed_markets WHERE market_id IN ({placeholders})",
            tuple(market_ids)
        ).fetchall()
    return {row['market_id']: row['last_price'] for row in rows if row['last_price'] is not None}

def get_recently_analyzed_market_ids(within_seconds: int = 1800) -> list:
    """Возвращает список ID рынков, которые были проанализированы в течение последних N секунд."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT market_id FROM analyzed_markets 
            WHERE analyzed_at > datetime('now', '-' || ? || ' seconds')
        """, (within_seconds,))
        return [row['market_id'] for row in cursor.fetchall()]

def get_markets_on_cooldown(cooldown_hours: int = 4) -> set:
    """Возвращает set market_id, проанализированных менее N часов назад."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT market_id FROM analyzed_markets 
            WHERE analyzed_at > datetime('now', '-' || ? || ' hours')
        """, (cooldown_hours,))
        return {row['market_id'] for row in cursor.fetchall()}

def save_telegram_post(chat_id: str, message_id: int, text: str) -> int:
    """Сохраняет пост из Telegram и возвращает его ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO telegram_posts (chat_id, message_id, text) VALUES (?, ?, ?)",
            (chat_id, message_id, text)
        )
        if cursor.rowcount > 0:
            return cursor.lastrowid
        else:
            # Если уже существует
            cursor.execute("SELECT id FROM telegram_posts WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
            row = cursor.fetchone()
            return row['id'] if row else None

def get_telegram_post_text(post_id: int) -> Optional[str]:
    with get_connection() as conn:
        cursor = conn.execute("SELECT text FROM telegram_posts WHERE id = ?", (post_id,))
        row = cursor.fetchone()
        return row['text'] if row else None

def get_telegram_post_info(post_id: int) -> dict:
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM telegram_posts WHERE id = ?", (post_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}

def mark_telegram_post_status(post_id: int, status: str):
    with get_connection() as conn:
        conn.execute("UPDATE telegram_posts SET status = ? WHERE id = ?", (status, post_id))

def save_memory(key: str, value: Any, category: str = 'general', ttl: int = None, priority: int = 0):
    """
    Сохраняет данные в долгосрочную Key-Value память (JSON).
    
    :param key: Ключ записи
    :param value: Значение (будет сохранено как JSON)
    :param category: Категория ('config', 'fact', 'preference', 'cache', 'general')
    :param ttl: Время жизни в секундах (None = бессрочно)
    :param priority: Приоритет для ранжирования (0-10, выше = важнее)
    """
    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    expires_at_str = None
    if ttl is not None:
        from datetime import timedelta
        expires_at_str = (now_dt + timedelta(seconds=ttl)).strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memory (key, value, updated_at, category, ttl, priority, expires_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?) 
               ON CONFLICT(key) DO UPDATE SET 
                   value=excluded.value, updated_at=excluded.updated_at,
                   category=excluded.category, ttl=excluded.ttl, 
                   priority=excluded.priority, expires_at=excluded.expires_at""",
            (key, json.dumps(value), now_str, category, ttl, priority, expires_at_str)
        )

def get_memory(key: str, default: Any = None) -> Any:
    """Извлекает данные из долгосрочной памяти. Пропускает истёкшие записи."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM memory WHERE key = ? AND (expires_at IS NULL OR expires_at > datetime('now'))",
            (key,)
        )
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row['value'])
            except json.JSONDecodeError:
                return row['value']
        return default

def get_active_facts(limit: int = 30) -> list:
    """Загружает актуальные приоритетные факты. Обёртка для обратной совместимости."""
    return get_relevant_facts(limit=limit)

def get_relevant_facts(context_keywords: list = None, limit: int = 20) -> list:
    """
    Возвращает факты для системного промпта, релевантные текущему контексту.
    Всегда включает config-факты (category='config', 'preference').
    При наличии ключевых слов добавляет релевантные факты из category='fact'.
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        # Всегда грузим config + preferences (высокий priority)
        cursor.execute("""
            SELECT key, value FROM memory
            WHERE (expires_at IS NULL OR expires_at > datetime('now'))
              AND category IN ('config', 'preference', 'fact', 'general')
              AND category != 'cache'
            ORDER BY priority DESC, updated_at DESC
            LIMIT ?
        """, (limit // 2 if context_keywords else limit,))
        base_facts = list(cursor.fetchall())

        contextual = []
        if context_keywords:
            for kw in context_keywords[:3]:
                cursor.execute(r"""
                    SELECT key, value FROM memory
                    WHERE (expires_at IS NULL OR expires_at > datetime('now'))
                      AND category = 'fact'
                      AND (key LIKE ? ESCAPE '\' OR value LIKE ? ESCAPE '\')
                    ORDER BY priority DESC LIMIT 5
                """, (f'%{_escape_like(kw)}%', f'%{_escape_like(kw)}%'))
                contextual.extend(cursor.fetchall())

        all_facts = base_facts + contextual
        results = []
        seen = set()
        for row in all_facts:
            if row['key'] not in seen:
                seen.add(row['key'])
                try:
                    val = json.loads(row['value'])
                except (json.JSONDecodeError, TypeError):
                    val = row['value']
                results.append(f"- {row['key']}: {val}")

        return results[:limit]

def cleanup_expired_memory():
    """Удаляет записи с истёкшим TTL из таблицы memory."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at < datetime('now')")
        count = cursor.rowcount
    return count

def upsert_known_whale(address: str, alias: str, win_rate: float,
                       total_profit: float = 0.0, total_vol: float = 0.0,
                       force_insider: bool = False) -> None:
    """Добавляет или обновляет известного кита (whale) в базе данных. force_insider=True только для вручную верифицированных адресов."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO wallets (address, alias, win_rate, total_profit, is_insider, last_seen)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(address) DO UPDATE SET
                alias=excluded.alias,
                win_rate=excluded.win_rate,
                total_profit=excluded.total_profit,
                is_insider=excluded.is_insider,
                last_seen=CURRENT_TIMESTAMP
        """, (address.lower(), alias, win_rate, total_profit, force_insider))



def add_wallet(address: str, alias: str = None, is_insider: bool = False):
    """Добавляет новый кошелек для мониторинга агентом SHADOW."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wallets (address, alias, is_insider, last_seen) VALUES (?, ?, ?, ?)",
            (address, alias, is_insider, datetime.now(timezone.utc))
        )

def update_wallet_stats(address: str, win_rate: float, total_profit: float, is_insider: bool = False):
    """Обновляет статистику кошелька (Win Rate) для фильтра Smart Money."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE wallets SET win_rate = ?, total_profit = ?, is_insider = ?, last_seen = ? WHERE address = ?",
            (win_rate, total_profit, is_insider, datetime.now(timezone.utc), address)
        )


def update_wallet_pvalue(
    address: str,
    n_trades: int,
    n_wins: int,
    p_value: float,
    is_insider: bool
) -> None:
    """Обновляет статистику p-value и статус инсайдера для кошелька."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE wallets
               SET n_trades=?, n_wins=?, p_value=?, is_insider=?, last_seen=CURRENT_TIMESTAMP
               WHERE address=?""",
            (n_trades, n_wins, p_value, is_insider, address.lower())
        )


def get_wallets_for_pvalue_recalc() -> list[dict]:
    """Возвращает кошельки с достаточным числом транзакций для пересчёта p-value."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT w.address, w.alias, w.n_trades, w.n_wins,
                   COUNT(t.id) as tx_count,
                   SUM(CASE WHEN t.outcome = m.outcome THEN 1 ELSE 0 END) as computed_wins
            FROM wallets w
            LEFT JOIN trader_transactions t ON w.address = t.wallet_address
            LEFT JOIN markets m ON t.market_id = m.id AND m.outcome IS NOT NULL
            GROUP BY w.address
            HAVING tx_count > 0 OR w.n_trades > 0
        """)

        return [dict(r) for r in cursor.fetchall()]

def add_discussion_message(market_id: str, agent_name: str, message: str, confidence: float = None, agree: bool = True):
    """Записывает мнение агента в общий журнал обсуждений."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_opinions (market_id, agent_name, opinion, confidence, agree) VALUES (?, ?, ?, ?, ?)",
            (market_id, agent_name, message, confidence, agree)
        )

def get_market_discussions(market_id: str):
    """Получает всю историю обсуждений агентов по конкретному рынку."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM agent_opinions WHERE market_id = ? ORDER BY created_at ASC",
            (market_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def cleanup_stale_signals(days: int = 365) -> int:
    """Переносит истёкшие рынки в историю и удаляет сигналы старше N дней."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Жесткое удаление старых сигналов
        cursor.execute("DELETE FROM signals WHERE created_at < datetime('now', ? || ' days')", (f"-{days}",))
        deleted_old = cursor.rowcount
        
        # 2. Перенос закрытых рынков в историю (status = ARCHIVED)
        cursor.execute("""
            UPDATE signals SET status = 'ARCHIVED'
            WHERE status = 'PENDING' AND market_id IN (
                SELECT id FROM markets WHERE datetime(close_time) < datetime('now')
            )
        """)
        archived_expired = cursor.rowcount
        
        # 3. Перенос прошлогодних (fallback)
        stale_year = str(datetime.now(timezone.utc).year - 1)
        cursor.execute(r"""
            UPDATE signals SET status = 'ARCHIVED'
            WHERE status = 'PENDING' AND market_id IN (
                SELECT id FROM markets WHERE title LIKE ? ESCAPE '\'
            )
        """, (f'%{_escape_like(stale_year)}%',))
        archived_year = cursor.rowcount
        
    return archived_expired + archived_year

def get_history_signals(limit: int = 100):
    """Получает завершенные сигналы (ARCHIVED, WIN, LOSS) для команды /history"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, m.title, m.url, m.price as market_price 
            FROM signals s 
            JOIN markets m ON s.market_id = m.id 
            WHERE s.status IN ('ARCHIVED', 'WIN', 'LOSS')
            ORDER BY s.created_at DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]



def update_vault_index(path: str, category: str, title: str, tags: list = None, content_hash: str = None):
    """Обновляет индекс vault-файла в SQLite для быстрого поиска."""
    import json as _json
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO vault_index (path, category, title, tags, updated_at, content_hash)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(path) DO UPDATE SET 
                category=excluded.category, title=excluded.title,
                tags=excluded.tags, updated_at=excluded.updated_at,
                content_hash=excluded.content_hash
        """, (path, category, title, _json.dumps(tags or []), content_hash))

def delete_vault_index(path: str):
    """Удаляет индекс конкретного файла из SQLite vault_index."""
    with get_connection() as conn:
        conn.execute("DELETE FROM vault_index WHERE path = ?", (path,))

def search_vault_index(query: str, limit: int = 10) -> list:
    """
    Быстрый поиск по индексу vault (по заголовку и тегам).
    Возвращает список совпадений [{path, category, title, tags}].
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(r"""
            SELECT path, category, title, tags FROM vault_index
            WHERE title LIKE ? ESCAPE '\' OR tags LIKE ? ESCAPE '\' OR path LIKE ? ESCAPE '\'
            ORDER BY updated_at DESC
            LIMIT ?
        """, (f'%{_escape_like(query)}%', f'%{_escape_like(query)}%', f'%{_escape_like(query)}%', limit))
        return [dict(row) for row in cursor.fetchall()]

def get_memory_stats() -> dict:
    """Возвращает метрики использования памяти для /status."""
    import os as _os
    stats = {}
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM memory WHERE expires_at IS NULL OR expires_at > datetime('now')")
            stats['facts'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM signals WHERE status = 'PENDING'")
            stats['signals_pending'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM signals WHERE status = 'ARCHIVED'")
            stats['signals_archived'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM markets")
            stats['markets'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM agent_opinions")
            stats['opinions'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM vault_index")
            stats['vault_files'] = cursor.fetchone()[0]
        # Размер файла БД
        if _os.path.exists(DB_PATH):
            stats['db_size_kb'] = _os.path.getsize(DB_PATH) / 1024
        else:
            stats['db_size_kb'] = 0
    except Exception as e:
        logger.error(f"[DB] Ошибка получения статистики памяти: {e}")
        return {}
    return stats

# --- Price History ---

def save_price_point(market_id: str, price: float):
    """Записывает точку цены для трендового анализа."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (market_id, price) VALUES (?, ?)",
            (market_id, price)
        )

def get_price_history(market_id: str, hours: int = 24) -> list:
    """Возвращает историю цен за последние N часов."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT price, recorded_at FROM price_history
            WHERE market_id = ? AND recorded_at > datetime('now', '-' || ? || ' hours')
            ORDER BY recorded_at ASC
        """, (market_id, hours))
        return [{'price': row['price'], 'recorded_at': row['recorded_at']} for row in cursor.fetchall()]

def cleanup_old_price_history(days: int = 7) -> int:
    """Удаляет старую историю цен (старше N дней)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM price_history WHERE recorded_at < datetime('now', '-' || ? || ' days')",
            (days,)
        )
        count = cursor.rowcount
    return count

def cleanup_old_episodes(days: int = 90) -> int:
    """Удаляет старые эпизоды из таблицы agent_episodes."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM agent_episodes WHERE created_at < datetime('now', '-' || ? || ' days')",
            (days,)
        )
        count = cursor.rowcount
    return count

# --- Correlations ---

def save_correlation(corr: MarketCorrelation):
    """Сохраняет обнаруженную корреляцию между рынками."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO correlations (market_id_a, market_id_b, title_a, title_b, correlation_type, description, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (corr.market_id_a, corr.market_id_b, corr.title_a, corr.title_b,
              corr.correlation_type, corr.description, corr.confidence))

def get_new_correlations() -> list:
    """Возвращает непрочитанные корреляции (для алертов в Telegram)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, market_id_a, market_id_b, title_a, title_b,
                   correlation_type, description, confidence, detected_at
            FROM correlations
            WHERE notified = 0
            ORDER BY detected_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_market_correlations(market_id: str) -> list:
    """Возвращает все известные корреляции для конкретного рынка."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, market_id_a, market_id_b, title_a, title_b,
                   correlation_type, description, confidence
            FROM correlations
            WHERE market_id_a = ? OR market_id_b = ?
            ORDER BY detected_at DESC
            LIMIT 5
        """, (market_id, market_id))
        return [dict(row) for row in cursor.fetchall()]

def mark_correlations_notified(ids: list):
    """Помечает корреляции как отправленные."""
    if not ids:
        return
    with get_connection() as conn:
        placeholders = ','.join('?' * len(ids))
        conn.execute(
            f"UPDATE correlations SET notified = 1 WHERE id IN ({placeholders})",
            ids
        )

# --- Episodic Memory ---

def save_agent_episode(
    agent_name: str,
    event_type: str,
    summary: str,
    market_id: str = None,
    market_title: str = None,
    context=None,
    outcome: str = "unknown"
) -> int:
    import json
    if isinstance(context, dict):
        context_str = json.dumps(context, ensure_ascii=False)
    elif isinstance(context, str):
        try:
            json.loads(context)
            context_str = context
        except (json.JSONDecodeError, TypeError):
            context_str = json.dumps(context)
    else:
        context_str = json.dumps({})

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO agent_episodes
            (agent_name, event_type, market_id, market_title, summary, context, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            agent_name, event_type, market_id, market_title,
            summary, context_str, outcome
        ))
        return cursor.lastrowid

def get_agent_episodes(
    agent_name: str = None,
    event_type: str = None,
    outcome: str = None,
    limit: int = 10
) -> list:
    """Возвращает историю эпизодов агента с фильтрами."""
    query = "SELECT * FROM agent_episodes WHERE 1=1"
    params = []
    if agent_name:
        query += " AND agent_name = ?"
        params.append(agent_name)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if outcome:
        query += " AND outcome = ?"
        params.append(outcome)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

def update_episode_outcome(episode_id: int, outcome: str):
    """Обновляет исход эпизода после закрытия рынка."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE agent_episodes SET outcome = ? WHERE id = ?",
            (outcome, episode_id)
        )

def get_agent_accuracy(agent_name: str) -> dict:
    """Возвращает статистику точности агента по завершённым эпизодам."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) as correct,
                SUM(CASE WHEN outcome='incorrect' THEN 1 ELSE 0 END) as incorrect
            FROM agent_episodes
            WHERE agent_name = ? AND outcome != 'unknown'
        """, (agent_name,))
        row = cursor.fetchone()
        total = row['total'] or 0
        correct = row['correct'] or 0
        return {
            "total": total,
            "correct": correct,
            "incorrect": row['incorrect'] or 0,
            "accuracy": round(correct / total, 3) if total > 0 else None
        }

def save_trader_transaction(wallet_address: str, market_id: str, outcome: str, amount_usd: float, price: float = None, alias: str = None):
    """
    Сохраняет транзакцию трейдера. Если кошелек отсутствует в wallets,
    автоматически добавляет его.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Проверяем / добавляем кошелек в wallets
        cursor.execute("SELECT address FROM wallets WHERE address = ?", (wallet_address,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO wallets (address, alias, last_seen) VALUES (?, ?, ?)",
                (wallet_address, alias, datetime.now(timezone.utc))
            )
        else:
            cursor.execute(
                "UPDATE wallets SET last_seen = ? WHERE address = ?",
                (datetime.now(timezone.utc), wallet_address)
            )
            if alias:
                cursor.execute(
                    "UPDATE wallets SET alias = ? WHERE address = ? AND alias IS NULL",
                    (alias, wallet_address)
                )
                
        # 2. Добавляем транзакцию
        cursor.execute("""
            INSERT INTO trader_transactions (wallet_address, market_id, outcome, amount_usd, price)
            VALUES (?, ?, ?, ?, ?)
        """, (wallet_address, market_id, outcome, amount_usd, price))
        

def get_market_trader_transactions(market_id: str, limit: int = 200) -> list:
    """
    Возвращает список крупных сделок трейдеров по конкретному рынку.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.*, w.alias, w.win_rate 
            FROM trader_transactions t
            JOIN wallets w ON t.wallet_address = w.address
            WHERE t.market_id = ?
            ORDER BY t.timestamp DESC
            LIMIT ?
        """, (market_id, limit))
        return [dict(row) for row in cursor.fetchall()]

def get_known_whales() -> dict:
    """Возвращает словарь {address: {alias, win_rate, total_won, total_vol, is_insider, n_trades, n_wins, p_value}} известных китов."""
    whales = {}
    with get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT w.address, w.alias, w.win_rate, w.total_profit, w.is_insider,
                       w.n_trades, w.n_wins, w.p_value,
                       COALESCE(t.total_vol, 0.0) as total_vol
                FROM wallets w
                LEFT JOIN (
                    SELECT wallet_address, SUM(amount_usd) as total_vol
                    FROM trader_transactions
                    GROUP BY wallet_address
                ) t ON w.address = t.wallet_address
            """)
            for row in cursor.fetchall():
                total_won = row["total_profit"] or 0.0
                total_vol = row["total_vol"] or 0.0
                if total_vol < total_won:
                    total_vol = total_won
                whales[row["address"]] = {
                    "alias": row["alias"],
                    "win_rate": row["win_rate"],
                    "total_won": total_won,
                    "total_profit": total_won,
                    "total_vol": total_vol,
                    "is_insider": bool(row["is_insider"]),
                    "n_trades": row["n_trades"] or 0,
                    "n_wins": row["n_wins"] or 0,
                    "p_value": row["p_value"] or 1.0
                }

        except Exception as e:
            logger.error(f"[DB] Ошибка при чтении wallets для known_whales: {e}")
    return whales



def get_performance_summary(agent_name: str, limit: int = 20) -> str:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT summary, outcome, context, created_at
            FROM agent_episodes
            WHERE agent_name = ? AND event_type = 'signal_resolved'
              AND outcome IN ('correct', 'incorrect')
            ORDER BY created_at DESC
            LIMIT ?
        """, (agent_name, limit))
        episodes = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) as correct
            FROM agent_episodes
            WHERE agent_name = ? AND event_type = 'signal_resolved'
              AND outcome IN ('correct', 'incorrect')
        """, (agent_name,))
        stats = cursor.fetchone()

    if not episodes:
        return ""
    
    total = stats['total'] or 0
    correct = stats['correct'] or 0
    accuracy = correct / total if total > 0 else 0
    
    lines = [
        f"## Твоя история прогнозов (последние {len(episodes)} из {total} разрешённых):",
        f"Точность: {correct}/{total} = {accuracy:.0%}\n",
        "Последние результаты:"
    ]
    for ep in episodes[:10]:
        icon = "✅" if ep['outcome'] == 'correct' else "❌"
        raw_ctx = ep['context'] or '{}'
        ctx = raw_ctx
        for _ in range(3):
            if not isinstance(ctx, str):
                break
            try:
                ctx = json.loads(ctx)
            except (json.JSONDecodeError, TypeError):
                ctx = {}
                break
        if not isinstance(ctx, dict):
            ctx = {}
        prob = ctx.get('predicted_prob', '?')
        prob_str = f"{prob:.0%}" if isinstance(prob, float) else str(prob)
        lines.append(f"{icon} {ep['summary'][:120]} [прогноз был: {prob_str}]")
    
    return "\n".join(lines)


def get_learning_impact() -> dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                lc.had_performance_ctx,
                COUNT(*) as total,
                SUM(CASE WHEN ae.outcome='correct' THEN 1 ELSE 0 END) as correct
            FROM llm_calls lc
            JOIN agent_episodes ae ON lc.market_id = ae.market_id
            WHERE ae.outcome IN ('correct', 'incorrect')
              AND lc.agent_name = ae.agent_name
            GROUP BY lc.had_performance_ctx
        """)
        rows = cursor.fetchall()
    result = {}
    for row in rows:
        key = "with_ctx" if row['had_performance_ctx'] else "without_ctx"
        total = row['total'] or 0
        correct = row['correct'] or 0
        result[key] = {
            "total": total, "correct": correct,
            "accuracy": round(correct / total, 3) if total > 0 else None
        }
    return result

def save_gate_metrics(run_id: str, total: int, passed: int,
                      blocked_no_volume: int, blocked_no_whales: int) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO gate_metrics
            (run_id, total, passed, blocked_no_volume, blocked_no_whales)
            VALUES (?, ?, ?, ?, ?)
        """, (run_id, total, passed, blocked_no_volume, blocked_no_whales))


def get_gate_metrics_last_n(n: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, run_id, total, passed, blocked_no_volume, blocked_no_whales, created_at 
            FROM gate_metrics 
            ORDER BY created_at DESC 
            LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]


# ─── Функции Penny Stocks ───────────────────────────────────

def add_penny_stock_to_monitoring(market_id: str, title: str, url: str, initial_price: float,
                                  predicted_outcome: str = None, edge: float = None, confidence: float = None) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO penny_stocks_monitoring
            (market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen,
             predicted_outcome, edge, confidence, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            ON CONFLICT(market_id) DO UPDATE SET
                predicted_outcome = CASE WHEN excluded.predicted_outcome IS NOT NULL THEN excluded.predicted_outcome ELSE predicted_outcome END,
                edge = CASE WHEN excluded.edge IS NOT NULL THEN excluded.edge ELSE edge END,
                confidence = CASE WHEN excluded.confidence IS NOT NULL THEN excluded.confidence ELSE confidence END
        """, (market_id, title, url, initial_price, initial_price, initial_price, initial_price,
              predicted_outcome, edge, confidence))

def get_active_penny_stocks() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen,
                   volume_2h, predicted_outcome, edge, confidence, status, spike_alert_sent, added_at,
                   virtual_bought_price, virtual_bought_at
            FROM penny_stocks_monitoring
            WHERE status = 'ACTIVE' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
            ORDER BY added_at DESC
        """).fetchall()
    return [dict(r) for r in rows]

def update_penny_stock_price(market_id: str, price: float, volume_2h: float = 0.0) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE penny_stocks_monitoring
            SET current_price = ?,
                max_price_seen = MAX(max_price_seen, ?),
                min_price_seen = MIN(min_price_seen, ?),
                volume_2h = ?
            WHERE market_id = ?
        """, (price, price, price, volume_2h, market_id))

def mark_penny_spike_sent(market_id: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE penny_stocks_monitoring
            SET spike_alert_sent = 1
            WHERE market_id = ?
        """, (market_id,))

def buy_virtual_penny_stock(market_id: str, price: float) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE penny_stocks_monitoring
            SET virtual_bought_price = ?,
                virtual_bought_at = CURRENT_TIMESTAMP
            WHERE market_id = ?
        """, (price, market_id))

def sell_virtual_penny_stock(market_id: str) -> None:
    with get_connection() as conn:
        # 1. Считываем данные рынка из мониторинга
        row = conn.execute("""
            SELECT title, url, initial_price, current_price, predicted_outcome, virtual_bought_price, virtual_bought_at
            FROM penny_stocks_monitoring
            WHERE market_id = ?
        """, (market_id,)).fetchone()
        
        if row and row['virtual_bought_price'] is not None:
            v_bought = row['virtual_bought_price']
            v_bought_at = row['virtual_bought_at']
            v_curr = row['current_price']
            
            # Определяем направление сделки
            pred = row['predicted_outcome']
            init_p = row['initial_price']
            if pred is not None:
                outcome_to_track = pred
            else:
                outcome_to_track = 'NO' if init_p >= 0.90 else 'YES'
                
            # Считаем цены исхода
            if outcome_to_track == 'NO':
                bought_outcome = 1.0 - v_bought
                curr_outcome = 1.0 - v_curr
            else:
                bought_outcome = v_bought
                curr_outcome = v_curr
                
            pnl_cents = curr_outcome - bought_outcome
            pnl_percent = (pnl_cents / bought_outcome * 100) if bought_outcome > 0 else 0.0
            
            # Записываем сделку в историю
            conn.execute("""
                INSERT INTO penny_virtual_trades_history (
                    market_id, title, url, outcome, bought_price, bought_outcome_price, 
                    sold_price, sold_outcome_price, pnl_cents, pnl_percent, bought_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, row['title'], row['url'], outcome_to_track,
                v_bought, bought_outcome, v_curr, curr_outcome,
                pnl_cents, pnl_percent, v_bought_at
            ))
            
        # 2. Очищаем портфель
        conn.execute("""
            UPDATE penny_stocks_monitoring
            SET virtual_bought_price = NULL,
                virtual_bought_at = NULL
            WHERE market_id = ?
        """, (market_id,))

def resolve_penny_stock(market_id: str, actual_outcome: str) -> None:
    with get_connection() as conn:
        # 1. Проверяем, был ли рынок в виртуальном портфеле
        row = conn.execute("""
            SELECT title, url, initial_price, predicted_outcome, virtual_bought_price, virtual_bought_at
            FROM penny_stocks_monitoring
            WHERE market_id = ?
        """, (market_id,)).fetchone()
        
        if row and row['virtual_bought_price'] is not None:
            v_bought = row['virtual_bought_price']
            v_bought_at = row['virtual_bought_at']
            
            # Определяем направление сделки
            pred = row['predicted_outcome']
            init_p = row['initial_price']
            if pred is not None:
                outcome_to_track = pred
            else:
                outcome_to_track = 'NO' if init_p >= 0.90 else 'YES'
                
            # Считаем цену входа целевого исхода
            bought_outcome = (1.0 - v_bought) if outcome_to_track == 'NO' else v_bought
            
            # При разрешении (resolve) цена исхода становится либо 1.0 (выиграли), либо 0.0 (проиграли)
            if actual_outcome == outcome_to_track:
                sold_outcome = 1.0
            else:
                sold_outcome = 0.0
                
            # Вычисляем соответствующую YES-цену при разрешении
            if outcome_to_track == 'NO':
                v_sold = 1.0 - sold_outcome  # т.е. если sold_outcome=1.0 (NO выиграл), то YES-цена = 0.0
            else:
                v_sold = sold_outcome        # т.е. если sold_outcome=1.0 (YES выиграл), то YES-цена = 1.0
                
            pnl_cents = sold_outcome - bought_outcome
            pnl_percent = (pnl_cents / bought_outcome * 100) if bought_outcome > 0 else 0.0
            
            # Записываем сделку в историю
            conn.execute("""
                INSERT INTO penny_virtual_trades_history (
                    market_id, title, url, outcome, bought_price, bought_outcome_price, 
                    sold_price, sold_outcome_price, pnl_cents, pnl_percent, bought_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, row['title'], row['url'], outcome_to_track,
                v_bought, bought_outcome, v_sold, sold_outcome,
                pnl_cents, pnl_percent, v_bought_at
            ))
            
        # 2. Обновляем статус рынка на RESOLVED и очищаем виртуальную покупку
        conn.execute("""
            UPDATE penny_stocks_monitoring
            SET status = 'RESOLVED',
                actual_outcome = ?,
                resolved_at = CURRENT_TIMESTAMP,
                virtual_bought_price = NULL,
                virtual_bought_at = NULL
            WHERE market_id = ?
        """, (actual_outcome, market_id))

def get_penny_stocks_stats() -> dict:
    with get_connection() as conn:
        row_total = conn.execute("""
            SELECT COUNT(*) as cnt FROM penny_stocks_monitoring
            WHERE (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
        """).fetchone()
        row_active = conn.execute("""
            SELECT COUNT(*) as cnt FROM penny_stocks_monitoring 
            WHERE status = 'ACTIVE' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
        """).fetchone()
        row_resolved = conn.execute("""
            SELECT COUNT(*) as cnt FROM penny_stocks_monitoring 
            WHERE status = 'RESOLVED' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
        """).fetchone()
        row_correct = conn.execute("""
            SELECT COUNT(*) as cnt FROM penny_stocks_monitoring 
            WHERE status = 'RESOLVED' AND UPPER(predicted_outcome) = UPPER(actual_outcome) AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90)
            )
        """).fetchone()
        row_avg_edge = conn.execute("""
            SELECT AVG(edge) as avg_edge FROM penny_stocks_monitoring 
            WHERE edge IS NOT NULL AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90)
            )
        """).fetchone()
        
    total = row_total["cnt"] if row_total else 0
    active = row_active["cnt"] if row_active else 0
    resolved = row_resolved["cnt"] if row_resolved else 0
    correct = row_correct["cnt"] if row_correct else 0
    avg_edge = row_avg_edge["avg_edge"] if row_avg_edge and row_avg_edge["avg_edge"] is not None else 0.0
    
    win_rate = (correct / resolved) if resolved > 0 else 0.0
    return {
        "total": total,
        "active": active,
        "resolved": resolved,
        "correct": correct,
        "win_rate": win_rate,
        "avg_edge": avg_edge
    }

def get_penny_stocks_history(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT market_id, title, url, initial_price, current_price, max_price_seen, min_price_seen,
                   predicted_outcome, actual_outcome, edge, confidence, added_at, resolved_at
            FROM penny_stocks_monitoring
            WHERE status = 'RESOLVED' AND (
                (predicted_outcome = 'YES' AND initial_price <= 0.10) OR
                (predicted_outcome = 'NO' AND initial_price >= 0.90) OR
                (predicted_outcome IS NULL AND (initial_price <= 0.10 OR initial_price >= 0.90))
            )
            ORDER BY resolved_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def upsert_compound_opportunity(opp: dict) -> bool:
    """Возвращает True если запись НОВАЯ (не дубликат)."""
    close_time_str = opp["close_time"]
    if isinstance(close_time_str, datetime):
        close_time_str = close_time_str.strftime("%Y-%m-%d %H:%M:%S")
        
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM compound_opportunities WHERE id = ?", (opp["id"],)
        ).fetchone()
        if existing:
            return False
        conn.execute("""
            INSERT INTO compound_opportunities
              (id, market_id, title, url, price, volume_usd, close_time,
               hours_left, spread_pct, roi_net_pct, confidence,
               obviousness_reason, status, outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, CURRENT_TIMESTAMP)
        """, (
            opp["id"], opp["market_id"], opp["title"], opp["url"],
            opp["price"], opp["volume_usd"], close_time_str,
            opp["hours_left"], opp.get("spread_pct"), opp["roi_net_pct"],
            opp["confidence"], opp.get("obviousness_reason"),
            opp.get("outcome", "YES"),
        ))
        return True

def save_compound_opportunity(opp) -> None:
    """Сохраняет FavouriteOpportunity в таблицу compound_opportunities."""
    close_time_str = opp.close_time.isoformat() if hasattr(opp.close_time, 'isoformat') else str(opp.close_time)
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM compound_opportunities WHERE id = ?", (opp.opp_id,)
        ).fetchone()
        if existing:
            return
        conn.execute("""
            INSERT INTO compound_opportunities 
              (id, market_id, title, url, price, volume_usd, close_time,
               hours_left, spread_pct, roi_net_pct, confidence, obviousness_reason,
               status, outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, CURRENT_TIMESTAMP)
        """, (
            opp.opp_id, opp.market_id, opp.title, opp.url,
            opp.price, opp.volume_usd, close_time_str,
            opp.hours_left, opp.spread_pct, opp.roi_net_pct,
            opp.confidence, opp.obviousness_reason, opp.outcome
        ))

def get_compound_opportunities(limit: int = 20) -> list[dict]:
    """Возвращает актуальные compound-возможности (не старше 24 часов)."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM compound_opportunities
            WHERE created_at >= datetime('now', '-24 hours')
              AND status = 'NEW'
            ORDER BY roi_net_pct DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def get_active_compound_opportunities() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM compound_opportunities
            WHERE status IN ('NEW', 'ALERTED', 'BOUGHT')
              AND datetime(close_time) > datetime('now')
            ORDER BY roi_net_pct DESC
        """).fetchall()
    return [dict(r) for r in rows]

def mark_compound_alerted(opp_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE compound_opportunities SET status='ALERTED', alerted_at=datetime('now') WHERE id=?",
            (opp_id,)
        )

def mark_compound_bought(opp_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE compound_opportunities SET status='BOUGHT' WHERE id=?",
            (opp_id,)
        )
        opp = conn.execute("SELECT * FROM compound_opportunities WHERE id = ?", (opp_id,)).fetchone()
        if opp:
            existing = conn.execute(
                "SELECT id FROM signals WHERE market_id = ? AND strategy_type = 'FAVOURITE_COMPOUND' AND status = 'PENDING'",
                (opp["market_id"],)
            ).fetchone()
            if not existing:
                opp_dict = dict(opp)
                conn.execute("""
                    INSERT INTO signals
                      (id, type, market_id, platform, edge, confidence, priority, summary, details, status, target_outcome, estimated_probability, strategy_type, market_price_at_signal, created_at)
                    VALUES (?, 'FAVOURITE_COMPOUND', ?, 'polymarket', 0.15, ?, 'high', ?, ?, 'PENDING', ?, ?, 'FAVOURITE_COMPOUND', ?, CURRENT_TIMESTAMP)
                """, (
                    f"fc_{opp_dict['market_id']}_{opp_id.split('_')[-1]}",
                    opp_dict["market_id"],
                    opp_dict["confidence"],
                    f"Favourite Compounding: {opp_dict['title']}",
                    opp_dict["obviousness_reason"] or "",
                    opp_dict.get("outcome", "YES"),
                    opp_dict["confidence"],
                    opp_dict["price"]
                ))

def resolve_compound_opportunity(opp_id: str, outcome: str, pnl_usd: float, exit_price: float = None) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE compound_opportunities
            SET status='RESOLVED', actual_outcome=?, pnl_usd=?, exit_price=?,
                resolved_at=datetime('now')
            WHERE id=?
        """, (outcome, pnl_usd, exit_price, opp_id))

def get_compound_settings() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM compound_settings").fetchall()
    result = dict(COMPOUND_DEFAULTS)
    result.update({r["key"]: r["value"] for r in rows})
    return {k: float(v) if k not in ("enabled",) else int(v) for k, v in result.items()}

def save_compound_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO compound_settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )

def get_compound_stats() -> dict:
    with get_connection() as conn:
        row_total = conn.execute("SELECT COUNT(*) as total FROM compound_opportunities").fetchone()
        row_bought = conn.execute("SELECT COUNT(*) as bought FROM compound_opportunities WHERE status = 'BOUGHT'").fetchone()
        row_resolved = conn.execute("SELECT COUNT(*) as resolved FROM compound_opportunities WHERE status = 'RESOLVED'").fetchone()
        row_wins = conn.execute("SELECT COUNT(*) as wins FROM compound_opportunities WHERE status = 'RESOLVED' AND pnl_usd > 0").fetchone()
        row_pnl = conn.execute("SELECT SUM(pnl_usd) as pnl FROM compound_opportunities WHERE pnl_usd IS NOT NULL").fetchone()
        
        total = row_total["total"] if row_total else 0
        bought = row_bought["bought"] if row_bought else 0
        resolved = row_resolved["resolved"] if row_resolved else 0
        wins = row_wins["wins"] if row_wins else 0
        pnl = row_pnl["pnl"] if row_pnl and row_pnl["pnl"] is not None else 0.0
        
        win_rate = wins / resolved if resolved > 0 else 0.0
        return {
            "total": total,
            "bought": bought,
            "resolved": resolved,
            "win_rate": win_rate,
            "total_pnl": round(pnl, 2)
        }

def add_blacklist_tag(tag: str) -> bool:
    """Добавляет тег в черный список в нижнем регистре. Возвращает True, если добавлен."""
    tag_clean = str(tag).strip().lower()
    if not tag_clean:
        return False
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO blacklist_tags (tag) VALUES (?)",
                (tag_clean,)
            )
        logger.info(f"[Blacklist] Тег {tag_clean!r} добавлен в черный список.")
        return True
    except Exception as e:
        logger.error(f"[Blacklist] Ошибка добавления тега {tag_clean!r}: {e}")
        return False

def remove_blacklist_tag(tag: str) -> bool:
    """Удаляет тег из черного списка. Возвращает True, если удален."""
    tag_clean = str(tag).strip().lower()
    try:
        with get_connection() as conn:
            res = conn.execute(
                "DELETE FROM blacklist_tags WHERE tag = ?",
                (tag_clean,)
            )
            changes = res.rowcount
        if changes > 0:
            logger.info(f"[Blacklist] Тег {tag_clean!r} удален из черного списка.")
            return True
        return False
    except Exception as e:
        logger.error(f"[Blacklist] Ошибка удаления тега {tag_clean!r}: {e}")
        return False

def get_blacklist_tags() -> list[str]:
    """Возвращает список всех заблокированных тегов в нижнем регистре."""
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT tag FROM blacklist_tags").fetchall()
        return [r["tag"] for r in rows]
    except Exception as e:
        logger.error(f"[Blacklist] Ошибка получения черного списка тегов: {e}")
        return []

def get_strategy_first_signal_date(strategy_type: str) -> Optional[datetime]:
    """Возвращает дату самого первого сигнала для заданной стратегии."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT MIN(created_at) as first_date FROM signals WHERE strategy_type = ?",
                (strategy_type,)
            ).fetchone()
            if row and row["first_date"]:
                date_str = row["first_date"]
                if "Z" in date_str:
                    date_str = date_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
    except Exception as e:
        logger.error(f"[DB] Ошибка получения даты первого сигнала для {strategy_type}: {e}")
    return None

if __name__ == "__main__":
    init_db()
