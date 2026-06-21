import sqlite3
import pytest
from unittest.mock import patch

from agents.shared.python.db import (
    save_agent_episode,
    get_agent_accuracy,
    get_learning_impact
)

@pytest.fixture
def memory_db():
    """Создает временную БД в памяти с таблицами memory, agent_episodes и llm_calls."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT DEFAULT 'general',
            ttl INTEGER DEFAULT NULL,
            priority INTEGER DEFAULT 0,
            expires_at DATETIME DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            event_type TEXT NOT NULL,
            market_id TEXT,
            market_title TEXT,
            summary TEXT NOT NULL,
            context TEXT,
            outcome TEXT DEFAULT 'unknown',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            had_performance_ctx INTEGER DEFAULT 0,
            market_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    
    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = conn
        mock_conn.return_value.__exit__.return_value = False
        yield conn
        
    conn.close()

def test_agent_accuracy_integration(memory_db):
    """Интеграционный тест: создание эпизодов -> проверка метрик точности."""
    # Сохраняем эпизоды
    save_agent_episode("SCOUT", "signal_evaluated", market_id="m1", summary="test", outcome="correct")
    save_agent_episode("SCOUT", "signal_evaluated", market_id="m2", summary="test", outcome="incorrect")
    save_agent_episode("SCOUT", "signal_evaluated", market_id="m3", summary="test", outcome="correct")
    
    # Считаем точность
    acc = get_agent_accuracy("SCOUT")
    
    assert acc["total"] == 3
    assert acc["correct"] == 2
    assert acc["incorrect"] == 1
    assert acc["accuracy"] == 0.667

def test_learning_impact_integration(memory_db):
    """Проверяем расчёт learning_impact на основе had_performance_ctx в llm_calls и исхода в agent_episodes."""
    # В llm_calls пишем что контекст был (had_performance_ctx=1) или не был (0)
    memory_db.execute("INSERT INTO llm_calls (agent_name, market_id, had_performance_ctx) VALUES ('SCOUT', 'm1', 1)")
    memory_db.execute("INSERT INTO llm_calls (agent_name, market_id, had_performance_ctx) VALUES ('SCOUT', 'm2', 0)")
    
    # Исход в agent_episodes
    save_agent_episode("SCOUT", "signal_evaluated", market_id="m1", summary="test", outcome="correct")
    save_agent_episode("SCOUT", "signal_evaluated", market_id="m2", summary="test", outcome="incorrect")
    
    impact = get_learning_impact()
    
    assert "with_ctx" in impact
    assert "without_ctx" in impact
    
    assert impact["with_ctx"]["total"] == 1
    assert impact["with_ctx"]["correct"] == 1
    assert impact["with_ctx"]["accuracy"] == 1.0
    
    assert impact["without_ctx"]["total"] == 1
    assert impact["without_ctx"]["correct"] == 0
    assert impact["without_ctx"]["accuracy"] == 0.0
