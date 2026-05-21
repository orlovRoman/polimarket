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
        # Инициализация через каноничный модуль (все таблицы, индексы)
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

    def get_memory(self, key: str) -> Optional[Any]:
        """Извлекает данные из долгосрочной памяти."""
        return _db.get_memory(key)


# Пример использования
if __name__ == "__main__":
    db = DatabaseManager()
    db.add_wallet("0x123456789", alias="PolymarketWhale1", is_insider=True)
    db.add_discussion_message("market_001", "SCOUT", "Нашел недооценку. Модель 45%, Рынок 20%.", confidence=0.85)
    print(f"База данных успешно инициализирована по пути: {db.db_path}")
