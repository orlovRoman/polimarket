from unittest.mock import AsyncMock
import pytest
import sqlite3
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import agents.shared.python.db as db
from agents.shared.python.db import archive_signal_by_id
from telegram.bot import send_ideas_page, callback_delete_signal

@pytest.fixture
def temp_db():
    """Создает временную БД в памяти с нужными таблицами."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Создаем таблицы
    conn.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            id TEXT PRIMARY KEY,
            platform TEXT,
            title TEXT,
            description TEXT,
            url TEXT,
            outcome TEXT,
            price REAL,
            close_time TEXT,
            tokens TEXT,
            volume REAL
        )
    """)
    conn.execute("""
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
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_id) REFERENCES markets (id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_lists (
            market_id TEXT PRIMARY KEY,
            market_title TEXT,
            list_type TEXT,
            base_price REAL,
            last_price REAL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Заполняем тестовыми данными
    conn.execute("INSERT INTO markets (id, platform, title, url, price) VALUES ('mkt_1', 'polymarket', 'Test Market 1', 'http://url1', 0.55)")
    conn.execute("INSERT INTO markets (id, platform, title, url, price) VALUES ('mkt_2', 'polymarket', 'Test Market 2', 'http://url2', 0.60)")
    
    conn.execute("""
        INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status)
        VALUES ('scout_mkt_1_1780000000', 'MISPRICING', 'mkt_1', 'polymarket', 0.1, 0.8, 'HIGH', 'Summary 1', 'Details 1', 'PENDING')
    """)
    conn.execute("""
        INSERT INTO signals (id, type, market_id, platform, edge, confidence, priority, summary, details, status)
        VALUES ('scout_mkt_2_1780000000', 'MISPRICING', 'mkt_2', 'polymarket', 0.15, 0.9, 'CRITICAL', 'Summary 2', 'Details 2', 'PENDING')
    """)
    conn.commit()
    
    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_conn.return_value = conn
        yield conn
        
    conn.close()

def test_archive_signal_by_id_full(temp_db):
    """Проверяем архивирование сигнала по полному ID."""
    assert archive_signal_by_id("scout_mkt_1_1780000000") is True
    
    cursor = temp_db.execute("SELECT status FROM signals WHERE id = 'scout_mkt_1_1780000000'")
    row = cursor.fetchone()
    assert row["status"] == "ARCHIVED"

def test_archive_signal_by_id_truncated(temp_db):
    """Проверяем архивирование сигнала по усеченному ID (LIKE-запрос)."""
    # Первые 30 символов от scout_mkt_2_1780000000
    truncated = "scout_mkt_2_1780000000"[:30]
    
    assert archive_signal_by_id(truncated) is True
    
    cursor = temp_db.execute("SELECT status FROM signals WHERE id = 'scout_mkt_2_1780000000'")
    row = cursor.fetchone()
    assert row["status"] == "ARCHIVED"

def test_archive_signal_not_found(temp_db):
    """Проверяем поведение при несуществующем ID."""
    assert archive_signal_by_id("scout_nonexistent") is False

def test_archive_signal_by_id_escapes_special_chars(temp_db):
    """signal_id с % и _ не должен матчить другие сигналы из-за LIKE."""
    temp_db.execute("""
        INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
        VALUES ('scout_mkt%1_1780000000', 'MISPRICING', 'mkt_1', 'polymarket', 0.8, 'HIGH', 'S1', 'D1', 'PENDING')
    """)
    temp_db.execute("""
        INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
        VALUES ('scout_mktXXX1_1780000000', 'MISPRICING', 'mkt_1', 'polymarket', 0.8, 'HIGH', 'S2', 'D2', 'PENDING')
    """)
    temp_db.commit()

    truncated = "scout_mkt%1_1780000000"[:30]
    assert archive_signal_by_id(truncated) is True

    cursor = temp_db.execute("SELECT status FROM signals WHERE id = 'scout_mktXXX1_1780000000'")
    assert cursor.fetchone()["status"] == "PENDING"

def test_remove_from_market_list_no_wildcard_bleed(temp_db):
    """remove_from_market_list не должна затрагивать другие рынки из-за спецсимволов LIKE."""
    from agents.shared.python.db import add_to_market_list, remove_from_market_list, is_in_market_list
    add_to_market_list("mkt%1", "Test %", "ignored")
    add_to_market_list("mkt_1", "Test _", "ignored")
    add_to_market_list("mkt_regular", "Test Regular", "ignored")

    remove_from_market_list("mkt%1")

    assert is_in_market_list("mkt%1", "ignored") is False
    assert is_in_market_list("mkt_1", "ignored") is True
    assert is_in_market_list("mkt_regular", "ignored") is True

def test_send_ideas_page_renders_buttons(temp_db):
    """Проверяем, что send_ideas_page правильно рендерит кнопки удаления."""
    async def run_test():
        # Создаем моки для message/callback
        mock_msg = AsyncMock()
        mock_msg.answer = AsyncMock()
        
        # Переопределим get_signals в боте или пропатчим в db
        mock_signals = [
            {
                "id": "scout_mkt_1_1780000000",
                "title": "Will Rihanna release a new album before GTA VI?",
                "edge": 0.12,
                "confidence": 0.75,
                "target_outcome": "YES",
                "market_price": 0.54,
                "summary": "Some summary here",
                "url": "http://gta6.com"
            }
        ]
        
        with patch("telegram.bot.get_signals", return_value=mock_signals), \
             patch("telegram.bot.send_or_edit") as mock_send:
             
            await send_ideas_page(mock_msg, page=0)
            
            mock_send.assert_called_once()
            args = mock_send.call_args[0]
            text_arg = args[1]
            keyboard_arg = args[2]
            
            # Проверяем, что в тексте сообщения есть ключевые эмодзи (1️⃣ вместо 📍)
            assert "1️⃣" in text_arg
            assert "Will Rihanna release a new album before GTA VI?" in text_arg
            
            # Проверяем структуру клавиатуры
            assert isinstance(keyboard_arg, InlineKeyboardMarkup)
            # Наша кнопка удаления должна быть первой
            del_button = keyboard_arg.inline_keyboard[0][0]
            assert "🗑️ Удалить 1:" in del_button.text
            # Callback data должен содержать truncated ID
            assert del_button.callback_data == "del_sig_0_scout_mkt_1_1780000000"[:30 + len("del_sig_0_")]
            
    asyncio.run(run_test())

def test_callback_delete_signal_flow():
    """Проверяем вызов callback_delete_signal."""
    async def run_test():
        mock_callback = AsyncMock()
        # callback data: del_sig_{page}_{truncated_id}
        mock_callback.data = "del_sig_2_scout_mkt_1_1780000000"[:35]
        mock_callback.answer = AsyncMock()
        
        with patch("telegram.bot.archive_signal_by_id", return_value=True) as mock_archive, \
             patch("telegram.bot.send_ideas_page") as mock_send_ideas:
             
            await callback_delete_signal(mock_callback)
            
            # Проверяем, что архивирование было вызвано с правильным ID
            expected_truncated_id = "scout_mkt_1_1780000000"[:30]
            mock_archive.assert_called_once_with(expected_truncated_id)
            
            # Проверяем отправку уведомления
            mock_callback.answer.assert_called_once_with("🗑️ Идея архивирована и убрана из списка.", show_alert=True)
            
            # Проверяем обновление страницы
            mock_send_ideas.assert_called_once_with(mock_callback, page=2)
            
    asyncio.run(run_test())

def test_archive_signal_exact_36_char_id(temp_db):
    """ID ровно 36 символов = UUID, должен идти через =, не LIKE."""
    uuid_id = "a" * 36
    temp_db.execute("""
        INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
        VALUES (?, 'MISPRICING', 'mkt_1', 'polymarket', 0.8, 'HIGH', 'S_UUID', 'D_UUID', 'PENDING')
    """, (uuid_id,))
    temp_db.commit()
    
    # Также добавим сигнал с префиксом UUID, чтобы убедиться, что LIKE не матчит его
    longer_id = uuid_id + "-extra"
    temp_db.execute("""
        INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
        VALUES (?, 'MISPRICING', 'mkt_1', 'polymarket', 0.8, 'HIGH', 'S_UUID_EXTRA', 'D_UUID_EXTRA', 'PENDING')
    """, (longer_id,))
    temp_db.commit()
    
    result = archive_signal_by_id(uuid_id)
    assert result is True
    
    # uuid_id должен быть архивирован
    row = temp_db.execute("SELECT status FROM signals WHERE id = ?", (uuid_id,)).fetchone()
    assert row['status'] == 'ARCHIVED'
    
    # longer_id не должен быть архивирован (так как поиск шел по = а не LIKE)
    row_longer = temp_db.execute("SELECT status FROM signals WHERE id = ?", (longer_id,)).fetchone()
    assert row_longer['status'] == 'PENDING'

def test_archive_signal_short_id_prefix_match(temp_db):
    """Короткий ID должен матчить через LIKE prefix."""
    full_id = "abc123xyz-full-signal-id"
    temp_db.execute("""
        INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
        VALUES (?, 'MISPRICING', 'mkt_1', 'polymarket', 0.8, 'HIGH', 'S_SHORT', 'D_SHORT', 'PENDING')
    """, (full_id,))
    temp_db.commit()
    
    result = archive_signal_by_id("abc123")  # префикс < 36 символов
    assert result is True
    
    row = temp_db.execute("SELECT status FROM signals WHERE id = ?", (full_id,)).fetchone()
    assert row['status'] == 'ARCHIVED'
