import sqlite3
import pytest
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import agents.shared.python.db as db
from agents.shared.python.db import save_memory, get_memory, cleanup_expired_memory, cleanup_old_episodes

@pytest.fixture
def temp_db():
    """Создает временную БД в памяти с таблицами memory, agent_episodes и триггерами."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Создаем таблицы
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
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_episodes_fts USING fts5(
            episode_id UNINDEXED,
            agent_name,
            summary,
            context
        )
    """)
    
    # Воссоздаем триггеры
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_episodes_ai_v2 AFTER INSERT ON agent_episodes BEGIN
            INSERT INTO agent_episodes_fts(episode_id, agent_name, summary, context) 
            VALUES (new.id, new.agent_name, new.summary, COALESCE(new.context, '{}'));
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_episodes_ad_v2 AFTER DELETE ON agent_episodes BEGIN
            DELETE FROM agent_episodes_fts WHERE episode_id = old.id;
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS agent_episodes_au_v2 AFTER UPDATE ON agent_episodes BEGIN
            DELETE FROM agent_episodes_fts WHERE episode_id = old.id;
            INSERT INTO agent_episodes_fts(episode_id, agent_name, summary, context) 
            VALUES (new.id, new.agent_name, new.summary, COALESCE(new.context, '{}'));
        END;
    """)
    
    conn.commit()
    
    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_conn.return_value = conn
        yield conn
        
    conn.close()

def test_save_memory_sqlite_format(temp_db):
    """Проверяем, что expires_at и updated_at сохраняются как строки %Y-%m-%d %H:%M:%S."""
    save_memory("test_key", "test_value", ttl=100)
    
    cursor = temp_db.execute("SELECT updated_at, expires_at FROM memory WHERE key = 'test_key'")
    row = cursor.fetchone()
    
    assert row is not None
    updated_at = row["updated_at"]
    expires_at = row["expires_at"]
    
    # Должны быть строками без T и timezone offset
    assert isinstance(updated_at, str)
    assert "T" not in updated_at
    assert "+" not in updated_at
    assert len(updated_at) == 19  # YYYY-MM-DD HH:MM:SS
    
    assert isinstance(expires_at, str)
    assert "T" not in expires_at
    assert "+" not in expires_at
    assert len(expires_at) == 19

def test_get_memory_ttl_expiry(temp_db):
    """Проверяем, что запись с истекшим TTL не считывается (сравнение с datetime('now') работает)."""
    # Записываем с ttl = -10 (уже истекла)
    save_memory("expired_key", "val", ttl=-10)
    # Записываем с ttl = 100 (еще активна)
    save_memory("active_key", "val", ttl=100)
    
    # get_memory не должен вернуть истекшую
    assert get_memory("expired_key") is None
    assert get_memory("active_key") == "val"
    
    # cleanup_expired_memory должен удалить expired_key
    deleted_count = cleanup_expired_memory()
    assert deleted_count == 1
    
    cursor = temp_db.execute("SELECT COUNT(*) FROM memory WHERE key = 'expired_key'")
    assert cursor.fetchone()[0] == 0

def test_agent_system_prompt_utc():
    """Проверяем, что _get_current_system_prompt оркестратора возвращает UTC время."""
    from agents.orchestrator.src.agent import NexusAgent
    
    with patch("agents.orchestrator.src.agent.os.getenv", return_value="FAKE_KEY"), \
         patch("agents.orchestrator.src.agent.DatabaseManager"), \
         patch("agents.orchestrator.src.agent.ObsidianAdapter"), \
         patch("agents.orchestrator.src.agent.NexusAgent._load_base_instructions", return_value="Base"):
         
        agent = NexusAgent()
        prompt = agent._get_current_system_prompt()
        
        assert "ТЕКУЩЕЕ ВРЕМЯ СИСТЕМЫ:" in prompt
        assert "UTC" in prompt

def test_fts_null_context(temp_db):
    """Проверяем, что вставка эпизода с context=None не вызывает ошибку в FTS-триггере."""
    temp_db.execute(
        "INSERT INTO agent_episodes (agent_name, event_type, summary, context) VALUES (?, ?, ?, ?)",
        ("NEXUS", "test", "Summary text", None)
    )
    temp_db.commit()
    
    # Проверяем, что FTS-таблица содержит запись
    cursor = temp_db.execute("SELECT context FROM agent_episodes_fts WHERE agent_name = 'NEXUS'")
    row = cursor.fetchone()
    assert row is not None
    assert row["context"] == "{}"  # COALESCE сработал

def test_cleanup_old_episodes(temp_db):
    """Проверяем очистку старых эпизодов."""
    # Записываем свежую запись
    temp_db.execute(
        "INSERT INTO agent_episodes (agent_name, event_type, summary, created_at) VALUES (?, ?, ?, datetime('now'))",
        ("NEXUS", "recent", "Recent action")
    )
    # Записываем старую запись (100 дней назад)
    temp_db.execute(
        "INSERT INTO agent_episodes (agent_name, event_type, summary, created_at) VALUES (?, ?, ?, datetime('now', '-100 days'))",
        ("NEXUS", "old", "Old action")
    )
    temp_db.commit()
    
    # Очищаем записи старше 90 дней
    deleted = cleanup_old_episodes(days=90)
    assert deleted == 1
    
    # Проверяем, что осталась только свежая запись
    cursor = temp_db.execute("SELECT event_type FROM agent_episodes")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "recent"

def test_update_episodes_for_market(temp_db):
    """Проверяем обновление outcome в agent_episodes на основе прогноза из context."""
    import json
    from agents.shared.python.db import update_episodes_for_market
    
    # 1. SCOUT эпизод
    temp_db.execute("""
        INSERT INTO agent_episodes (agent_name, event_type, market_id, market_title, summary, context, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "SCOUT", "signal_evaluated", "mkt-1", "Market 1", "Scout summary",
        json.dumps({"target_outcome": "YES", "agree": True}), "unknown"
    ))
    
    # 2. SWING эпизод
    temp_db.execute("""
        INSERT INTO agent_episodes (agent_name, event_type, market_id, market_title, summary, context, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "SWING", "signal_evaluated", "mkt-1", "Market 1", "Swing summary",
        json.dumps({"target_outcome": "NO"}), "unknown"
    ))
    
    # 3. SHADOW эпизод (agree = True)
    temp_db.execute("""
        INSERT INTO agent_episodes (agent_name, event_type, market_id, market_title, summary, context, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "SHADOW", "signal_evaluated", "mkt-1", "Market 1", "Shadow agree summary",
        json.dumps({"target_outcome": "YES", "agree": True}), "unknown"
    ))
    
    # 4. SHADOW эпизод (agree = False)
    temp_db.execute("""
        INSERT INTO agent_episodes (agent_name, event_type, market_id, market_title, summary, context, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        "SHADOW", "signal_evaluated", "mkt-1", "Market 1", "Shadow disagree summary",
        json.dumps({"target_outcome": "YES", "agree": False}), "unknown"
    ))
    
    temp_db.commit()
    
    # Резолвим рынок как YES
    update_episodes_for_market("mkt-1", "YES")
    
    # Считываем результаты
    cursor = temp_db.execute("SELECT agent_name, summary, outcome FROM agent_episodes ORDER BY id ASC")
    rows = cursor.fetchall()
    
    # SCOUT (target_outcome="YES", resolved="YES") -> correct
    assert rows[0]["agent_name"] == "SCOUT"
    assert rows[0]["outcome"] == "correct"
    
    # SWING (target_outcome="NO", resolved="YES") -> incorrect
    assert rows[1]["agent_name"] == "SWING"
    assert rows[1]["outcome"] == "incorrect"
    
    # SHADOW (agree=True, target_outcome="YES", resolved="YES") -> correct
    assert rows[2]["agent_name"] == "SHADOW"
    assert rows[2]["summary"] == "Shadow agree summary"
    assert rows[2]["outcome"] == "correct"
    
    # SHADOW (agree=False, target_outcome="YES", resolved="YES") -> incorrect
    assert rows[3]["agent_name"] == "SHADOW"
    assert rows[3]["summary"] == "Shadow disagree summary"
    assert rows[3]["outcome"] == "incorrect"


def test_save_signal_zero_probability(temp_db):
    """Проверяем, что при сохранении сигнала с estimated_probability=0.0 оно сохраняется как 0.0, а не confidence."""
    from core.models import Signal
    from agents.shared.python.db import save_signal
    
    # Создаем таблицу signals во временной БД
    temp_db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            type TEXT,
            market_id TEXT,
            platform TEXT,
            edge REAL,
            confidence REAL,
            priority TEXT,
            summary TEXT,
            details TEXT,
            status TEXT,
            created_at TIMESTAMP,
            target_outcome TEXT,
            estimated_probability REAL
        )
    """)
    temp_db.commit()
    
    # 1. Сигнал с estimated_probability=0.0
    sig = Signal(
        id="sig-zero-prob",
        type="SCOUT",
        market_id="mkt-zero",
        platform="polymarket",
        edge=0.1,
        confidence=0.8,
        priority="medium",
        summary="Zero prob summary",
        details="{}",
        estimated_probability=0.0
    )
    
    save_signal(sig)
    
    cursor = temp_db.execute("SELECT estimated_probability FROM signals WHERE id = 'sig-zero-prob'")
    row = cursor.fetchone()
    assert row is not None
    assert row["estimated_probability"] == 0.0
