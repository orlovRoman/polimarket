from unittest.mock import AsyncMock
"""
Дополнительные тесты для bagов BUG-1, BUG-2, BUG-3 в коммите 6ce8378.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock


# ══════════════════════════════════════════════════════════
# BUG-1: get_active_scan_status_text не падает при RuntimeError от CoreEngine
# ══════════════════════════════════════════════════════════

def test_get_active_scan_status_text_safe_when_engine_not_initialized():
    """
    get_active_scan_status_text() должна вернуть строку даже если CoreEngine
    бросает RuntimeError (например, GOOGLE_API_KEY не задан при первом вызове).
    BUG: без try/except функция упадёт с необработанным RuntimeError.
    FIX: обернуть CoreEngine() в try/except, вернуть fallback-строку.
    """
    from telegram.bot import get_active_scan_status_text

    with patch("core.engine.CoreEngine", side_effect=RuntimeError("GOOGLE_API_KEY не установлен")):
        try:
            result = get_active_scan_status_text()
            # После фикса: должна вернуть читаемую строку
            assert isinstance(result, str), "Должна вернуть строку, а не None"
            assert len(result) > 0, "Строка не должна быть пустой"
        except RuntimeError as e:
            pytest.fail(
                f"get_active_scan_status_text() подняла RuntimeError: {e}. "
                f"Нужно обернуть CoreEngine() в try/except и вернуть fallback-строку."
            )


def test_get_active_scan_status_text_safe_when_engine_raises_attribute_error():
    """
    Если engine.state не существует (AttributeError) — функция должна не упасть.
    """
    from telegram.bot import get_active_scan_status_text

    with patch("core.engine.CoreEngine") as mock_cls:
        mock_engine = MagicMock()
        del mock_engine.state  # AttributeError при обращении к .state
        mock_cls.return_value = mock_engine

        try:
            result = get_active_scan_status_text()
            assert isinstance(result, str)
        except AttributeError as e:
            pytest.fail(
                f"get_active_scan_status_text() подняла AttributeError: {e}. "
                f"Нужна защита от отсутствия engine.state."
            )


def test_get_active_scan_status_text_returns_fallback_contains_scanning_message():
    """
    Fallback-строка при ошибке CoreEngine должна содержать понятное сообщение
    о том, что сканирование запущено (не пустую строку и не traceback).
    """
    from telegram.bot import get_active_scan_status_text

    with patch("core.engine.CoreEngine", side_effect=Exception("DB locked")):
        try:
            result = get_active_scan_status_text()
            # Должна содержать что-то осмысленное
            assert any(word in result for word in ["сканирование", "запущено", "Сканирование", "🔄"]), (
                f"Fallback-строка не содержит ожидаемых слов: '{result}'"
            )
        except Exception as e:
            pytest.fail(f"Функция упала с исключением: {e}")


# ══════════════════════════════════════════════════════════
# BUG-2: disable_web_page_preview устарел в aiogram 3.x
# ══════════════════════════════════════════════════════════

def test_no_deprecated_disable_web_page_preview_in_bot_source():
    """
    В telegram/bot.py не должно быть устаревшего параметра disable_web_page_preview.
    aiogram 3.x использует link_preview_options=LinkPreviewOptions(is_disabled=True).
    
    ВАЖНО: этот тест намеренно ищет ТОЛЬКО в строках с answer/edit_text вызовами,
    не в тестовых файлах (где мы проверяем call_args).
    """
    import pathlib

    source = pathlib.Path("telegram/bot.py").read_text(encoding="utf-8")
    lines = source.splitlines()

    bad_lines = []
    for i, line in enumerate(lines, 1):
        # Ищем вызовы aiogram методов с устаревшим параметром
        if "disable_web_page_preview" in line and any(
            method in line for method in ["answer(", "edit_text(", "send_message(", "reply("]
        ):
            bad_lines.append((i, line.strip()))

    assert not bad_lines, (
        f"Найдено {len(bad_lines)} использований устаревшего 'disable_web_page_preview' "
        f"в вызовах aiogram методов:\n"
        + "\n".join(f"  Строка {ln}: {txt}" for ln, txt in bad_lines)
        + "\n\nЗамените на: link_preview_options=LinkPreviewOptions(is_disabled=True)\n"
        + "Импорт: from aiogram.types import LinkPreviewOptions"
    )


def test_link_preview_options_used_correctly():
    """
    Если в bot.py используется link_preview_options — проверяем корректный импорт.
    """
    import pathlib
    source = pathlib.Path("telegram/bot.py").read_text(encoding="utf-8")

    if "link_preview_options" in source:
        assert "LinkPreviewOptions" in source, (
            "link_preview_options используется, но LinkPreviewOptions не импортирован. "
            "Добавьте: from aiogram.types import LinkPreviewOptions"
        )


# ══════════════════════════════════════════════════════════
# BUG-3: test_command_status_handler_scanning — пробел в покрытии агентов
# ══════════════════════════════════════════════════════════

def test_command_status_handler_shows_agent_statuses_when_scanning():
    """
    Усиленная версия test_command_status_handler_scanning.
    Проверяет, что при активном сканировании /status показывает:
    - Блок "Детали текущего сканирования"
    - Категорию
    - Текущий рынок
    - Статусы SCOUT, SWING, SHADOW
    - Количество найденных идей
    BUG: оригинальный тест не проверял статусы агентов — они могли быть убраны.
    """
    async def run_test():
        mock_message = AsyncMock()

        fake_state = {
            "category": "⚽ Спорт",
            "stage": "Обсуждение (SCOUT + SWING + SHADOW)",
            "total_markets": 5,
            "current_market_index": 2,
            "current_market_title": "Ronaldo retires in 2026?",
            "current_market_url": "https://polymarket.com/ronaldo",
            "scout_status": "🟢 Edge (0.12)",
            "swing_status": "🚀 Ждет памп",
            "shadow_status": "⏳ Проверяет...",
            "ideas_found": 1,
        }

        with patch("telegram.bot._scan_lock") as mock_lock, \
             patch("core.engine.CoreEngine") as mock_engine_cls, \
             patch("agents.shared.python.db.get_connection") as mock_conn_ctx, \
             patch("agents.shared.python.db.get_memory_stats", return_value={
                 "facts": 10, "markets": 50, "signals_pending": 3,
                 "signals_archived": 20, "opinions": 100,
                 "vault_files": 5, "db_size_kb": 1024
             }), \
             patch("agents.shared.python.db.get_memory", return_value=None):

            mock_lock.locked.return_value = True

            mock_engine = MagicMock()
            mock_engine.state = fake_state
            mock_engine._scan_lock.locked.return_value = True
            mock_engine_cls.return_value = mock_engine

            # Мок get_connection как context manager
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = []
            mock_conn.cursor.return_value = mock_cursor
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn_ctx.return_value.__exit__ = MagicMock(return_value=False)

            from telegram.bot import command_status_handler
            await command_status_handler(mock_message)

        assert mock_message.answer.called, "answer() не был вызван"
        sent_text = mock_message.answer.call_args[0][0]

        # Проверяем основные разделы
        assert "Детали текущего сканирования" in sent_text, \
            "Блок деталей сканирования отсутствует в ответе /status"
        assert "⚽ Спорт" in sent_text, "Категория не отображается"
        assert "Ronaldo retires" in sent_text, "Текущий рынок не отображается"

        # BUG-3: эти проверки отсутствовали в оригинальном тесте
        assert "SCOUT" in sent_text, \
            "Статус SCOUT не отображается в /status при активном сканировании"
        assert "SWING" in sent_text, \
            "Статус SWING не отображается в /status при активном сканировании"
        assert "SHADOW" in sent_text, \
            "Статус SHADOW не отображается в /status при активном сканировании"
        assert "🟢 Edge (0.12)" in sent_text, \
            "Значение scout_status не отображается в /status"
        assert "ideas_found" in sent_text.lower() or "найдено идей" in sent_text.lower(), \
            "Счётчик найденных идей не отображается"

    asyncio.run(run_test())


def test_get_active_scan_status_text_includes_all_agent_fields():
    """
    get_active_scan_status_text() должна включать все три агента в вывод.
    Регрессионный тест: если убрать любой агент — тест упадёт.
    """
    from telegram.bot import get_active_scan_status_text

    fake_state = {
        "category": "₿ Крипто",
        "stage": "Обсуждение",
        "total_markets": 3,
        "current_market_index": 1,
        "current_market_title": "BTC reaches $200k?",
        "current_market_url": "https://polymarket.com/btc",
        "scout_status": "🟢 Edge (0.20)",
        "swing_status": "⚪️ Нет хайпа",
        "shadow_status": "❌ Против",
        "ideas_found": 0,
    }

    with patch("core.engine.CoreEngine") as mock_cls:
        mock_engine = MagicMock()
        mock_engine.state = fake_state
        mock_cls.return_value = mock_engine

        result = get_active_scan_status_text()

    for field in ["SCOUT", "SWING", "SHADOW", "₿ Крипто", "BTC reaches"]:
        assert field in result, (
            f"Поле '{field}' отсутствует в get_active_scan_status_text().\n"
            f"Полный вывод:\n{result}"
        )


# ══════════════════════════════════════════════════════════
# Интеграционный smoke-тест: get_active_scan_status_text — структура HTML
# ══════════════════════════════════════════════════════════

def test_get_active_scan_status_text_valid_html_tags():
    """
    Вывод функции не должен содержать незакрытые HTML-теги.
    Telegram parse_mode=HTML молча обрезает контент при неверной разметке.
    """
    from telegram.bot import get_active_scan_status_text
    import re

    fake_state = {
        "category": "Авто-микс",
        "stage": "Скрининг",
        "total_markets": 0,
        "current_market_index": 0,
        "current_market_title": "",
        "current_market_url": "",
        "scout_status": "⏳ Ожидает",
        "swing_status": "⏳ Ожидает",
        "shadow_status": "⏳ Ожидает",
        "ideas_found": 0,
    }

    with patch("core.engine.CoreEngine") as mock_cls:
        mock_engine = MagicMock()
        mock_engine.state = fake_state
        mock_cls.return_value = mock_engine

        result = get_active_scan_status_text()

    # Простая проверка: количество открывающих и закрывающих тегов <b> должно совпадать
    open_b = len(re.findall(r"<b>", result))
    close_b = len(re.findall(r"</b>", result))
    assert open_b == close_b, (
        f"Несбалансированные теги <b>: открывающих={open_b}, закрывающих={close_b}.\n"
        f"Telegram обрежет текст. Вывод:\n{result}"
    )

    open_a = len(re.findall(r"<a ", result))
    close_a = len(re.findall(r"</a>", result))
    assert open_a == close_a, (
        f"Несбалансированные теги <a>: открывающих={open_a}, закрывающих={close_a}.\n"
        f"Вывод:\n{result}"
    )
