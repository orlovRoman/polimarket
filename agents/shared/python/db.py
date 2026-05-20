import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Any
from .models import Market, Signal

DB_PATH = Path(__file__).parent.parent.parent.parent / "vault" / "database.sqlite"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализация таблиц базы данных"""
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
        
        conn.commit()
    print(f"База данных инициализирована по адресу: {DB_PATH}")

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
        return [{"role": row["role"], "parts": [{"text": row["content"]}]} for row in reversed(rows)]

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
        cursor.execute("""
            INSERT OR REPLACE INTO markets (id, platform, title, description, url, outcome, price, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (market.id, market.platform, market.title, market.description, market.url, market.outcome, market.price, market.close_time.isoformat()))
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

def save_memory(key: str, value: Any):
    """Сохраняет данные в долгосрочную Key-Value память (JSON)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), datetime.utcnow())
        )
        conn.commit()

if __name__ == "__main__":
    init_db()
