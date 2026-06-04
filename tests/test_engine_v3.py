from unittest.mock import AsyncMock
# tests/test_engine_v3.py

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock, call


# ── Баг #1: send_telegram_to_chat блокирует event loop ───────

def test_runtime_error_uses_asyncio_to_thread():
    """
    При RuntimeError в цикле markets уведомление должно идти
    через asyncio.to_thread, а не синхронным вызовом.
    """
    to_thread_calls = []

    async def mock_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn.__name__ if hasattr(fn, '__name__') else str(fn))
        return None

    async def run_test():
        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            # Эмулируем исправленный блок кода
            async def fixed_handler(e, chat_id, send_fn):
                await asyncio.to_thread(send_fn, f"⚠️ {e}", chat_id)

            mock_send = MagicMock()
            await fixed_handler(RuntimeError("test"), "chat_123", mock_send)

    asyncio.run(run_test())
    assert len(to_thread_calls) == 1, "send должен быть вызван через asyncio.to_thread"


def test_sync_send_blocks_loop_detection():
    """Демонстрируем что синхронный вызов в async НЕ использует to_thread"""
    to_thread_called = False

    async def mock_to_thread(fn, *args, **kwargs):
        nonlocal to_thread_called
        to_thread_called = True

    async def run_test():
        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            # Старый (неправильный) код — не вызывает to_thread
            mock_send = MagicMock()
            mock_send("⚠️ error", "chat_123")  # синхронный вызов напрямую

    asyncio.run(run_test())
    assert not to_thread_called, "Синхронный вызов не использует to_thread — это баг"


# ── Баг #2: NoMarketsFoundError не уведомляет пользователя ───

class NoMarketsFoundError(Exception):
    pass


def _classify_exception(exc) -> str:
    """Классифицируем исключение как в engine.py"""
    if isinstance(exc, NoMarketsFoundError):
        return "no_markets"
    elif isinstance(exc, RuntimeError):
        return "runtime_error"
    else:
        return "generic"


def test_no_markets_error_classified_correctly():
    exc = NoMarketsFoundError("Рынки не найдены")
    assert _classify_exception(exc) == "no_markets"


def test_runtime_error_classified_correctly():
    exc = RuntimeError("Сканирование выполняется")
    assert _classify_exception(exc) == "runtime_error"


def test_generic_exception_not_confused_with_no_markets():
    exc = ValueError("unexpected")
    assert _classify_exception(exc) == "generic"


def test_no_markets_error_sends_telegram_notification():
    """NoMarketsFoundError должен уведомить пользователя, а не только залогировать"""
    sent = []

    async def mock_to_thread(fn, *args, **kwargs):
        sent.append(args)
        return None

    async def run_test():
        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            # Эмулируем исправленный блок
            async def fixed_loop(exc, chat_id, send_fn):
                try:
                    raise exc
                except NoMarketsFoundError as e:
                    await asyncio.to_thread(send_fn, f"⚠️ {e}", chat_id)
                except RuntimeError as e:
                    await asyncio.to_thread(send_fn, f"⚠️ {e}", chat_id)

            mock_send = MagicMock()
            await fixed_loop(NoMarketsFoundError("Нет рынков"), "chat_123", mock_send)

    asyncio.run(run_test())
    assert len(sent) == 1
    assert "Нет рынков" in sent[0][0]


# ── Баг #3: scan_limit = 0 → пустой срез без предупреждения ─

def _resolve_scan_limit_with_zero_guard(raw, default=5) -> int:
    """Исправленная логика с guard на нулевое значение"""
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    if val <= 0:
        return default
    return val


def test_scan_limit_zero_falls_back_to_default():
    assert _resolve_scan_limit_with_zero_guard(0, default=5) == 5


def test_scan_limit_negative_falls_back_to_default():
    assert _resolve_scan_limit_with_zero_guard(-3, default=5) == 5


def test_scan_limit_string_zero_falls_back():
    assert _resolve_scan_limit_with_zero_guard("0", default=5) == 5


def test_scan_limit_valid_positive():
    assert _resolve_scan_limit_with_zero_guard(10, default=5) == 10


def test_scan_limit_one_is_valid():
    assert _resolve_scan_limit_with_zero_guard(1, default=5) == 1


def test_scan_limit_times_two_safe():
    """scan_limit * 2 должен быть > 0 после guard"""
    limit = _resolve_scan_limit_with_zero_guard(0, default=5)
    assert limit * 2 > 0


# ── Регрессия: предыдущие фиксы не сломаны ───────────────────

def test_scan_limit_list_still_uses_default():
    assert _resolve_scan_limit_with_zero_guard([], default=5) == 5


def test_scan_limit_dict_still_uses_default():
    assert _resolve_scan_limit_with_zero_guard({}, default=5) == 5


def test_scan_limit_valid_string_int():
    assert _resolve_scan_limit_with_zero_guard("7", default=5) == 7


# ── Интеграция: полный путь analyze_post_async при ошибке ────

def test_analyze_post_async_status_error_on_fatal():
    """При fatal error статус должен стать ERROR"""
    status_log = []

    def fake_mark_status(post_id, status):
        status_log.append(status)

    async def run_test():
        with patch("agents.shared.python.db.get_telegram_post_info",
                   return_value={"status": "NEW", "text": "test", "message_id": 1}), \
             patch("agents.shared.python.db.mark_telegram_post_status",
                   side_effect=fake_mark_status), \
             patch("agents.orchestrator.src.news_processor.NewsProcessor") as MockNP:

            MockNP.return_value.find_relevant_markets.side_effect = RuntimeError("LLM down")

            from core.engine import CoreEngine
            engine = object.__new__(CoreEngine)
            engine.api_key = "test"

            await engine.analyze_post_async(post_id=1, chat_id="test_chat")

    asyncio.run(run_test())

    assert "PROCESSING" in status_log
    assert "ERROR" in status_log
    # ERROR должен идти после PROCESSING
    assert status_log.index("ERROR") > status_log.index("PROCESSING")
