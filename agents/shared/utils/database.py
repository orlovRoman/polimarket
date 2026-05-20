import sqlite3
import os
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

class DatabaseManager:
    """
    Менеджер базы данных SQLite для мультиагентной системы Polymarket.
    Обеспечивает потокобезопасный (в рамках SQLite) доступ к общей памяти,
    истории обсуждений агентов и профилям кошельков (Smart Money).
    """

    def __init__(self, db_path: str = "/home/orlovrp/polymarket-bot/vault/database.sqlite"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        """Возвращает соединение с БД. Включает поддержку внешних ключей и возвращает строки как словари."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """Инициализирует таблицы базы данных, если они еще не существуют."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
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

            # Таблица: Обсуждения агентов (Shared State)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_opinions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    opinion TEXT NOT NULL,
                    confidence REAL,
                    agree BOOLEAN,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Таблица: Финальные торговые сигналы
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    market_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    edge REAL,
                    confidence REAL NOT NULL,
                    priority TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Таблица: Долгосрочная память (Key-Value)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memory (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL, -- JSON string
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()

    def add_wallet(self, address: str, alias: str = None, is_insider: bool = False):
        """Добавляет новый кошелек для мониторинга агентом SHADOW."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO wallets (address, alias, is_insider, last_seen) VALUES (?, ?, ?, ?)",
                (address, alias, is_insider, datetime.utcnow())
            )
            conn.commit()

    def update_wallet_stats(self, address: str, win_rate: float, total_profit: float):
        """Обновляет статистику кошелька (Win Rate) для фильтра Smart Money."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE wallets SET win_rate = ?, total_profit = ?, last_seen = ? WHERE address = ?",
                (win_rate, total_profit, datetime.utcnow(), address)
            )
            conn.commit()

    def add_discussion_message(self, market_id: str, agent_name: str, message: str, confidence: float = None, agree: bool = True):
        """Записывает мнение агента в общий журнал обсуждений."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO agent_opinions (market_id, agent_name, opinion, confidence, agree) VALUES (?, ?, ?, ?, ?)",
                (market_id, agent_name, message, confidence, agree)
            )
            conn.commit()

    def get_market_discussions(self, market_id: str) -> List[Dict[str, Any]]:
        """Получает всю историю обсуждений агентов по конкретному рынку."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_opinions WHERE market_id = ? ORDER BY created_at ASC",
                (market_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def save_memory(self, key: str, value: Any):
        """Сохраняет данные в долгосрочную Key-Value память (JSON)."""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), datetime.utcnow())
            )
            conn.commit()

    def get_memory(self, key: str) -> Optional[Any]:
        """Извлекает данные из долгосрочной памяти."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT value FROM memory WHERE key = ?", (key,))
            row = cursor.fetchone()
            return json.loads(row['value']) if row else None

# Пример использования
if __name__ == "__main__":
    db = DatabaseManager()
    db.add_wallet("0x123456789", alias="PolymarketWhale1", is_insider=True)
    db.add_discussion_message("market_001", "SCOUT", "Нашел недооценку. Модель 45%, Рынок 20%.", confidence=0.85)
    print(f"База данных успешно инициализирована по пути: {db.db_path}")
