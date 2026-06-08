"""
Тест: двойное нажатие "В идеи" не создаёт дубликатов сигналов в БД.
"""
import os
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

import config
import agents.shared.python.db as db_module
from core.models import Market

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база данных для теста."""
    db_path = tmp_path / "test_dedup.db"
    db_path_str = str(db_path)
    
    # Патчим DB_PATH в config и db_module
    monkeypatch.setattr(config, "DB_PATH", db_path_str)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    
    db_module.init_db()
    return db_path

def test_add_idea_no_duplicate(isolated_db, monkeypatch):
    # Добавляем тестовый рынок в изолированную БД
    from datetime import datetime, timezone
    market = Market(
        id="market_dedup_123",
        platform="polymarket",
        title="Will bitcoin hit 100k?",
        description="test description",
        url="https://polymarket.com/123",
        outcome="YES",
        price=0.6,
        close_time=datetime.now(timezone.utc),
        tokens=[],
        volume=1000.0,
        condition_id="cond_123"
    )
    db_module.save_market(market)
    
    # Подготовим callback'и
    callback1 = MagicMock()
    callback1.data = "add_idea_market_dedup_123"
    
    callback2 = MagicMock()
    callback2.data = "add_idea_market_dedup_123"
    
    async def async_noop(*args, **kwargs):
        return None
        
    callback1.answer.side_effect = async_noop
    callback1.message.edit_reply_markup.side_effect = async_noop
    callback2.answer.side_effect = async_noop
    callback2.message.edit_reply_markup.side_effect = async_noop
    
    from telegram.bot import callback_add_idea
    
    # Запускаем два параллельных вызова callback_add_idea
    async def run_parallel():
        await asyncio.gather(
            callback_add_idea(callback1),
            callback_add_idea(callback2)
        )
        
    asyncio.run(run_parallel())
    
    # Проверяем, сколько сигналов создалось в БД
    with db_module.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, market_id, status FROM signals")
        rows = cursor.fetchall()
        print("SIGNALS IN TEST DB:")
        for r in rows:
            print(f" - ID: {r['id']}, market_id: {r['market_id']}, status: {r['status']}")
        
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='signals'")
        indexes = cursor.fetchall()
        print("INDEXES IN TEST DB:")
        for name, sql in indexes:
            print(f" - {name}: {sql}")

        cursor.execute("SELECT COUNT(*) FROM signals WHERE market_id = 'market_dedup_123'")
        count = cursor.fetchone()[0]
        
    assert count == 1, f"Ожидался ровно 1 сигнал, но создано {count}!"

