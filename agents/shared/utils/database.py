"""
DatabaseManager — обёртка над каноничным модулем db.py.
Сохраняет OOP-интерфейс для обратной совместимости с NexusAgent,
memory_archiver, generate_daily и другими потребителями.
Вся реальная логика работы с БД живёт в agents.shared.python.db.
"""
import sys
from typing import Dict, Any, List, Optional
from pathlib import Path

# Импортируем путь из единого конфига
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import DB_PATH

# Импортируем каноничные функции из db.py для делегирования
from agents.shared.python import db as _db


class DatabaseManager:
    """
    Менеджер базы данных SQLite для мультиагентной системы Polymarket.
    Тонкая обёртка над каноничным модулем db.py.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        # init_db() имеет встроенный guard (_db_initialized) — безопасно вызывать повторно
        _db.init_db()

    def _get_connection(self):
        """Возвращает соединение с БД. Делегирует в db.get_connection()."""
        return _db.get_connection()

    # --- Кошельки (Smart Money) ---
    def add_wallet(self, address: str, alias: str = None, is_insider: bool = False):
        """Добавляет новый кошелек для мониторинга агентом SHADOW."""
        _db.add_wallet(address, alias, is_insider)

    def update_wallet_stats(self, address: str, win_rate: float, total_profit: float):
        """Обновляет статистику кошелька (Win Rate)."""
        _db.update_wallet_stats(address, win_rate, total_profit)

    # --- Обсуждения агентов ---
    def add_discussion_message(self, market_id: str, agent_name: str, message: str, confidence: float = None, agree: bool = True):
        """Записывает мнение агента в общий журнал обсуждений."""
        _db.add_discussion_message(market_id, agent_name, message, confidence, agree)

    def get_market_discussions(self, market_id: str) -> List[Dict[str, Any]]:
        """Получает всю историю обсуждений агентов по конкретному рынку."""
        return _db.get_market_discussions(market_id)

    # --- Долгосрочная память (Key-Value) ---
    def save_memory(self, key: str, value: Any):
        """Сохраняет данные в долгосрочную Key-Value память (JSON)."""
        _db.save_memory(key, value)

    def save_memory_full(self, key: str, value: Any, category: str = 'general', ttl: int = None, priority: int = 0):
        """Сохраняет данные в долгосрочную Key-Value память с полным набором параметров."""
        _db.save_memory(key, value, category=category, ttl=ttl, priority=priority)

    def get_memory(self, key: str, default: Any = None) -> Optional[Any]:
        """Извлекает данные из долгосрочной памяти."""
        return _db.get_memory(key, default)

    def delete_memory(self, key: str) -> None:
        """Удаляет ключ из долгосрочной памяти."""
        _db.delete_memory(key)

    # --- Токены и модели ---
    def get_token_usage_last_24h(self, agent_name: str) -> Dict[str, int]:
        """Получает статистику токенов агента за 24 часа."""
        return _db.get_token_usage_last_24h(agent_name)

    def get_agent_model(self, agent_name: str, default_model: str = "gemini-2.5-flash") -> str:
        """Получает последнюю использованную модель агента."""
        return _db.get_agent_model(agent_name, default_model)


    # --- Сигналы и рынки ---
    def save_signal(self, signal):
        """Сохраняет торговый сигнал."""
        _db.save_signal(signal)

    def save_market(self, market):
        """Сохраняет/обновляет данные о рынке."""
        _db.save_market(market)

    def get_signals(self, status: str = None) -> list:
        """Получает список сигналов."""
        return _db.get_signals(status)

    def delete_signal(self, signal_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
            conn.commit()

    def update_signal_status(self, signal_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
            conn.commit()

    def cleanup_expired_signals(self, before_iso: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE signals SET status = 'EXECUTED'
                WHERE status = 'PENDING' AND market_id IN (
                    SELECT id FROM markets WHERE close_time < ?
                )
            """, (before_iso,))
            conn.commit()
            return cursor.rowcount

    def execute_select(self, query: str) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
