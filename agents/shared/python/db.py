import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, List
from .models import Market, Signal, MarketCorrelation

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

_db_initialized = False

def init_db():
    """Инициализация таблиц базы данных (вызывается один раз)."""
    global _db_initialized
    if _db_initialized:
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
        # Таблица расхода токенов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_agent_date ON token_usage(agent_name, created_at)")

        conn.commit()
    _db_initialized = True
    print(f"База данных инициализирована по адресу: {DB_PATH}")

def save_token_usage(agent_name: str, model_name: str, input_tokens: int, output_tokens: int):
    """Сохраняет запись о потреблении токенов агентом."""
    total_tokens = input_tokens + output_tokens
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO token_usage (agent_name, model_name, input_tokens, output_tokens, total_tokens) VALUES (?, ?, ?, ?, ?)",
                (agent_name, model_name, input_tokens, output_tokens, total_tokens)
            )
            conn.commit()
    except Exception as e:
        print(f"[DB] Ошибка при сохранении расхода токенов: {e}")

def get_token_usage_last_24h(agent_name: str) -> dict:
    """Возвращает статистику потребления токенов агентом за последние 24 часа."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT SUM(input_tokens) as in_t, SUM(output_tokens) as out_t, SUM(total_tokens) as tot_t 
                   FROM token_usage 
                   WHERE agent_name = ? AND created_at >= datetime('now', '-1 day')""",
                (agent_name,)
            )
            row = cursor.fetchone()
            if row and row['tot_t'] is not None:
                return {
                    "input_tokens": int(row['in_t']),
                    "output_tokens": int(row['out_t']),
                    "total_tokens": int(row['tot_t'])
                }
    except Exception as e:
        print(f"[DB] Ошибка получения статистики токенов: {e}")
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

def get_agent_model(agent_name: str, default_model: str = "gemini-2.5-flash") -> str:
    """Возвращает последнюю использованную модель для агента из логов токенов."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT model_name FROM token_usage WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1",
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
        conn.commit()

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
    """
    Удаляет старые сообщения из истории чата, оставляя только последние keep_last записей.
    Согласно MEMORY_POLICY.md, мы не храним полные логи LLM-диалогов бесконечно.
    """
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
        conn.commit()

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
        conn.commit()

def save_signal(signal: Signal):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (signal.id, signal.type, signal.market_id, signal.platform, signal.edge, signal.confidence, signal.priority, signal.summary, signal.details, getattr(signal, 'status', 'PENDING'), signal.created_at.isoformat()))
        conn.commit()

def mark_market_analyzed(market_id: str, price: float):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO analyzed_markets (market_id, last_price, analyzed_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (market_id, price))
        conn.commit()

def get_last_analyzed_price(market_id: str) -> float | None:
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
        conn.commit()

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
    """
    Загружает актуальные приоритетные факты для системного промпта.
    Фильтрует по TTL, сортирует по приоритету. Не загружает category='cache'.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT key, value FROM memory 
            WHERE (expires_at IS NULL OR expires_at > datetime('now'))
              AND category != 'cache'
            ORDER BY priority DESC, updated_at DESC
            LIMIT ?
        """, (limit,))
        results = []
        for row in cursor.fetchall():
            try:
                val = json.loads(row['value'])
            except (json.JSONDecodeError, TypeError):
                val = row['value']
            results.append(f"- {row['key']}: {val}")
        return results

def cleanup_expired_memory():
    """Удаляет записи с истёкшим TTL из таблицы memory."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at < datetime('now')")
        count = cursor.rowcount
        conn.commit()
    return count

def add_wallet(address: str, alias: str = None, is_insider: bool = False):
    """Добавляет новый кошелек для мониторинга агентом SHADOW."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wallets (address, alias, is_insider, last_seen) VALUES (?, ?, ?, ?)",
            (address, alias, is_insider, datetime.now(timezone.utc))
        )
        conn.commit()

def update_wallet_stats(address: str, win_rate: float, total_profit: float):
    """Обновляет статистику кошелька (Win Rate) для фильтра Smart Money."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE wallets SET win_rate = ?, total_profit = ?, last_seen = ? WHERE address = ?",
            (win_rate, total_profit, datetime.now(timezone.utc), address)
        )
        conn.commit()

def add_discussion_message(market_id: str, agent_name: str, message: str, confidence: float = None, agree: bool = True):
    """Записывает мнение агента в общий журнал обсуждений."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO agent_opinions (market_id, agent_name, opinion, confidence, agree) VALUES (?, ?, ?, ?, ?)",
            (market_id, agent_name, message, confidence, agree)
        )
        conn.commit()

def get_market_discussions(market_id: str):
    """Получает всю историю обсуждений агентов по конкретному рынку."""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM agent_opinions WHERE market_id = ? ORDER BY created_at ASC",
            (market_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

def cleanup_stale_signals():
    """Архивирует устаревшие сигналы (рынки 2025 года и истёкшие рынки)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        # Архивируем сигналы рынков, которые уже закрыты
        cursor.execute("""
            UPDATE signals SET status = 'ARCHIVED'
            WHERE status = 'PENDING' AND market_id IN (
                SELECT id FROM markets WHERE close_time < datetime('now')
            )
        """)
        archived_expired = cursor.rowcount
        # Архивируем сигналы с прошлогодним годом в названии рынка (fallback)
        stale_year = str(datetime.now(timezone.utc).year - 1)
        cursor.execute("""
            UPDATE signals SET status = 'ARCHIVED'
            WHERE status = 'PENDING' AND market_id IN (
                SELECT id FROM markets WHERE title LIKE ?
            )
        """, (f'%{stale_year}%',))
        archived_year = cursor.rowcount
        conn.commit()
    return archived_expired + archived_year


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
        conn.commit()

def search_vault_index(query: str, limit: int = 10) -> list:
    """
    Быстрый поиск по индексу vault (по заголовку и тегам).
    Возвращает список совпадений [{path, category, title, tags}].
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT path, category, title, tags FROM vault_index
            WHERE title LIKE ? OR tags LIKE ? OR path LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (f'%{query}%', f'%{query}%', f'%{query}%', limit))
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
        conn.commit()

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
        conn.commit()
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
        conn.commit()

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
        conn.commit()

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
        
        conn.commit()

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


if __name__ == "__main__":
    init_db()
