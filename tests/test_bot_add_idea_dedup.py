"""
Тест: двойное нажатие "В идеи" не создаёт дубликатов сигналов в БД.
"""
import os
import sys
import asyncio
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.models import Signal

def test_add_idea_no_duplicate():
    # 1. Создаем временный файл БД
    db_fd, db_path_str = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    db_path = Path(db_path_str)
    
    try:
        # Патчим путь к БД в config
        import config
        original_db_path = getattr(config, "DB_PATH", "polymarket_bot.db")
        config.DB_PATH = db_path_str
        
        # Импортируем db и bot
        import agents.shared.python.db as db
        # Сбросим флаги инициализации
        db._db_initialized = False
        db.init_db()
        
        # Добавляем тестовый рынок
        from core.models import Market
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
        db.save_market(market)
        
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
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM signals WHERE market_id = 'market_dedup_123'")
            count = cursor.fetchone()[0]
            
        assert count == 1, f"Ожидался ровно 1 сигнал, но создано {count}!"
        
    finally:
        # Восстанавливаем оригинальный путь
        config.DB_PATH = original_db_path
        if db_path.exists():
            try:
                db_path.unlink()
            except Exception:
                pass
