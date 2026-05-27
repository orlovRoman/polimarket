# services/signal_evaluator.py
"""
Сервис автоматической оценки точности сигналов после закрытия рынков.
Запускается по расписанию раз в 6 часов из main.py.
"""
from config import logger
from agents.shared.python.db import (
    get_connection, save_agent_episode, get_memory, save_memory
)
from agents.shared.adapters.polymarket import PolymarketAdapter


"""
DEPRECATED: логика оценки сигналов перенесена в agents/shared/python/resolution.py
            (функция resolve_closed_markets). Этот файл НЕ должен вызываться.
"""

def evaluate_closed_signals() -> dict:
    raise DeprecationWarning(
        "evaluate_closed_signals() устарела. "
        "Используй resolution.resolve_closed_markets() — она теперь "
        "обновляет episodic memory и accuracy stats."
    )
