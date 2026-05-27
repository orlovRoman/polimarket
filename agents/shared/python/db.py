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

@contextmanager
def get_connection():
    """Контекст-менеджер для SQLite-соединений с гарантированным закрытием."""
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

def init_db():
    """Инициализация таблиц базы данных. Thread-safe, вызывается один раз."""
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:  # double-check после получения лока
            return
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
                    VALUES (new.id, new.agent_name, new.summary, new.context);
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
                    VALUES (new.id, new.agent_name, new.summary, new.context);
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

            # Индексы для ускорения частых запросов
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_opinions_market ON agent_opinions(market_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_chat ON chat_history(chat_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_close ON markets(close_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_market ON price_history(market_id, recorded_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_correlations_new ON correlations(notified, detected_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trader_transactions_market ON trader_transactions(market_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_agent ON agent_episodes(agent_name, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodes_outcome ON agent_episodes(outcome, event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_idea_audit_created_at ON idea_audit (created_at)")
            # Duplicate idx_signals_status index removed
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_category ON memory (category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_markets_close_time ON markets (close_time)")

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

            # Миграция: добавляем новые колонки в markets (tokens, volume)
            market_cols = {row[1] for row in cursor.execute("PRAGMA table_info(markets)").fetchall()}
            if 'tokens' not in market_cols:
                cursor.execute("ALTER TABLE markets ADD COLUMN tokens TEXT DEFAULT NULL")
            if 'volume' not in market_cols:
                cursor.execute("ALTER TABLE markets ADD COLUMN volume REAL DEFAULT NULL")
            
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

        _db_initialized = True
        print(f"База данных инициализирована по адресу: {DB_PATH}")

def save_cross_arbitrage(signal) -> None:
    """Сохраняет или обновляет кросс-платформенный арбитражный сигнал."""
    init_db()
    with get_connection() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO cross_arbitrage_signals
            (id, market_a_id, market_a_platform, market_a_title, market_a_price, market_a_url,
             market_b_id, market_b_platform, market_b_title, market_b_price, market_b_url,
             has_arbitrage, arbitrage_type, spread_percent, reasoning, trade_instruction,
             match_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"{signal.market_a_id}__{signal.market_b_id}",
            signal.market_a_id, signal.market_a_platform, signal.market_a_title,
            signal.market_a_price, signal.market_a_url,
            signal.market_b_id, signal.market_b_platform, signal.market_b_title,
            signal.market_b_price, signal.market_b_url,
            int(signal.has_arbitrage), signal.arbitrage_type, signal.spread_percent,
            signal.reasoning, signal.trade_instruction,
            signal.match_score, signal.status,
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


def save_idea_audit(market_id: str, market_title: str, audit_data: dict):
    """Сохраняет аудит-запись о прохождении идеи через pipeline."""
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
        print(f"[DB] Ошибка получения последней модели агента: {e}")
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
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chat_history WHERE chat_id = ?", (chat_id,))
        count = cursor.fetchone()[0]

    if count > summarize_threshold:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT role, content FROM chat_history
                WHERE chat_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (chat_id, count - keep_last))
            old_msgs = [f"{r['role']}: {r['content'][:200]}" for r in cursor.fetchall()]

        if old_msgs:
            summary = (f"[Архив диалога от {datetime.now().strftime('%Y-%m-%d')}]: "
                       + " | ".join(old_msgs[:10]))
            save_memory(
                key=f"chat_archive_{chat_id}_{datetime.now().strftime('%Y%m%d')}",
                value=summary,
                category='episodic',
                priority=5,
                ttl=30 * 24 * 3600
            )

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM chat_history
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM chat_history
                WHERE chat_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            )
        """, (chat_id, chat_id, keep_last))

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

def save_market(market: Market):
    with get_connection() as conn:
        cursor = conn.cursor()
        tokens_json = json.dumps(market.tokens) if market.tokens else None
        cursor.execute("""
            INSERT OR REPLACE INTO markets (id, platform, title, description, url, outcome, price, close_time, tokens, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (market.id, market.platform, market.title, market.description, market.url, market.outcome, market.price, market.close_time.isoformat(), tokens_json, market.volume))

def save_signal(signal: Signal, details_obj=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        details_str = details_obj.model_dump_json() if details_obj else signal.details
        
        target_outcome = details_obj.target_outcome if details_obj else 'YES'
        estimated_prob = details_obj.estimated_probability if details_obj else signal.confidence
        
        cursor.execute("""
            INSERT OR REPLACE INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at, target_outcome, estimated_probability)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (signal.id, signal.type, signal.market_id, signal.platform, signal.edge, signal.confidence, signal.priority, signal.summary, details_str, getattr(signal, 'status', 'PENDING'), signal.created_at.isoformat(), target_outcome, estimated_prob))

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
    now = datetime.now(timezone.utc)
    expires_at = None
    if ttl is not None:
        from datetime import timedelta
        expires_at = (now + timedelta(seconds=ttl)).isoformat()
    
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO memory (key, value, updated_at, category, ttl, priority, expires_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?) 
               ON CONFLICT(key) DO UPDATE SET 
                   value=excluded.value, updated_at=excluded.updated_at,
                   category=excluded.category, ttl=excluded.ttl, 
                   priority=excluded.priority, expires_at=excluded.expires_at""",
            (key, json.dumps(value), now, category, ttl, priority, expires_at)
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
                cursor.execute("""
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

def add_wallet(address: str, alias: str = None, is_insider: bool = False):
    """Добавляет новый кошелек для мониторинга агентом SHADOW."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wallets (address, alias, is_insider, last_seen) VALUES (?, ?, ?, ?)",
            (address, alias, is_insider, datetime.now(timezone.utc))
        )

def update_wallet_stats(address: str, win_rate: float, total_profit: float):
    """Обновляет статистику кошелька (Win Rate) для фильтра Smart Money."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE wallets SET win_rate = ?, total_profit = ?, last_seen = ? WHERE address = ?",
            (win_rate, total_profit, datetime.now(timezone.utc), address)
        )

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

def cleanup_stale_signals():
    """Переносит истёкшие рынки в историю и удаляет сигналы старше 1 года."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Жесткое удаление старше 1 года (365 дней)
        cursor.execute("DELETE FROM signals WHERE created_at < datetime('now', '-365 days')")
        deleted_old = cursor.rowcount
        
        # 2. Перенос закрытых рынков в историю (status = ARCHIVED)
        cursor.execute("""
            UPDATE signals SET status = 'ARCHIVED'
            WHERE status = 'PENDING' AND market_id IN (
                SELECT id FROM markets WHERE close_time < datetime('now')
            )
        """)
        archived_expired = cursor.rowcount
        
        # 3. Перенос прошлогодних (fallback)
        stale_year = str(datetime.now(timezone.utc).year - 1)
        cursor.execute("""
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

def search_vault_index(query: str, limit: int = 10) -> list:
    """
    Быстрый поиск по индексу vault (по заголовку и тегам).
    Возвращает список совпадений [{path, category, title, tags}].
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
        print(f"[DB] Ошибка получения статистики памяти: {e}")
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
            WHERE notified = FALSE
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
            f"UPDATE correlations SET notified = TRUE WHERE id IN ({placeholders})",
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
        

def get_market_trader_transactions(market_id: str, limit: int = 50) -> list:
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
    """Возвращает словарь {address: {alias, win_rate, total_won, total_vol}} известных китов."""
    whales = {}
    with get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT address, alias, win_rate, total_won, total_vol FROM known_whales")
            for row in cursor.fetchall():
                whales[row["address"]] = {
                    "alias": row["alias"],
                    "win_rate": row["win_rate"],
                    "total_won": row["total_won"],
                    "total_vol": row["total_vol"]
                }
        except Exception as e:
            print(f"[DB] Ошибка при чтении known_whales: {e}")
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
        try:
            ctx = raw_ctx
            while isinstance(ctx, str):
                ctx = json.loads(ctx)
            if not isinstance(ctx, dict):
                ctx = {}
        except (json.JSONDecodeError, TypeError):
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

if __name__ == "__main__":
    init_db()
