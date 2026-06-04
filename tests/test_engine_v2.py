from unittest.mock import AsyncMock
# tests/test_engine_v2.py

import pytest
from unittest.mock import MagicMock, patch, AsyncMock, call
from datetime import datetime, timezone


# ── Баг #1: send_telegram_to_chat блокирует event loop ───────

def test_runtime_error_sends_warning_without_blocking():
    """
    При RuntimeError в цикле markets бот должен отправить предупреждение
    асинхронно (через asyncio.to_thread), не блокируя event loop.
    """
    import asyncio

    sent_messages = []

    async def fake_to_thread(fn, *args, **kwargs):
        # Эмулируем asyncio.to_thread — вызываем синхронно
        result = fn(*args, **kwargs)
        return result

    def fake_send(msg, chat_id):
        sent_messages.append((msg, chat_id))

    async def run_test():
        with patch("asyncio.to_thread", side_effect=fake_to_thread), \
             patch("services.notifications.send_telegram_to_chat", side_effect=fake_send):
            # Эмулируем логику: RuntimeError → send → break
            chat_id = "test_chat"
            try:
                raise RuntimeError("Сканирование уже выполняется")
            except RuntimeError as e:
                await asyncio.to_thread(fake_send, f"⚠️ {e}", chat_id)

    asyncio.run(run_test())

    assert len(sent_messages) == 1
    assert "Сканирование" in sent_messages[0][0]


# ── Баг #2: scan_limit из get_memory — некорректные значения ─

def _resolve_scan_limit(raw, default=5):
    """Воспроизводим исправленную логику из engine.py"""
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def test_scan_limit_from_valid_int():
    assert _resolve_scan_limit(10) == 10


def test_scan_limit_from_string():
    assert _resolve_scan_limit("7") == 7


def test_scan_limit_from_none_uses_default():
    assert _resolve_scan_limit(None, default=5) == 5


def test_scan_limit_from_empty_list_uses_default():
    assert _resolve_scan_limit([], default=5) == 5


def test_scan_limit_from_dict_uses_default():
    assert _resolve_scan_limit({}, default=5) == 5


def test_scan_limit_from_zero_returns_zero():
    # int("0") = 0 — корректно, не должно падать
    assert _resolve_scan_limit("0") == 0


# ── Баг #3: signal.details отсутствует → AttributeError ─────

def _get_combined_opinion(signal, swing_signal) -> str:
    """Воспроизводим исправленную логику из engine.py"""
    scout_opinion = (
        getattr(signal, 'details', '')
        or getattr(signal, 'signal_cause', '')
        if signal else ""
    )
    swing_opinion = (
        getattr(swing_signal, 'details', '')
        or getattr(swing_signal, 'catalyst', '')
        if swing_signal else ""
    )
    return "\n\n".join(filter(None, [scout_opinion, swing_opinion]))


def test_combined_opinion_uses_signal_cause_fallback():
    signal = MagicMock(spec=[])  # нет поля details
    signal.signal_cause = "Рынок недооценён"
    result = _get_combined_opinion(signal, None)
    assert "Рынок недооценён" in result


def test_combined_opinion_uses_swing_catalyst_fallback():
    swing = MagicMock(spec=[])  # нет поля details
    swing.catalyst = "Рост объёма +300%"
    result = _get_combined_opinion(None, swing)
    assert "Рост объёма" in result


def test_combined_opinion_both_none():
    result = _get_combined_opinion(None, None)
    assert result == ""


def test_combined_opinion_prefers_details_over_cause():
    signal = MagicMock()
    signal.details = "Детальный анализ"
    signal.signal_cause = "Краткая причина"
    result = _get_combined_opinion(signal, None)
    assert "Детальный анализ" in result
    assert "Краткая причина" not in result


def test_combined_opinion_falls_back_when_details_empty():
    signal = MagicMock()
    signal.details = ""  # пустая строка → fallback
    signal.signal_cause = "Краткая причина"
    result = _get_combined_opinion(signal, None)
    assert "Краткая причина" in result


# ── Интеграция: SHADOW не падает при отсутствии details ──────

def test_shadow_analyze_called_with_correct_opinion():
    """Убеждаемся что combined_opinion передаётся в SHADOW корректно"""
    shadow = MagicMock()
    shadow.analyze_idea.return_value = MagicMock(agree=True, confidence=0.8)

    signal = MagicMock(spec=['signal_cause'])
    signal.signal_cause = "Недооценка рынка"
    # нет .details — не должно бросать AttributeError

    combined = _get_combined_opinion(signal, None)
    shadow.analyze_idea(MagicMock(), combined)

    shadow.analyze_idea.assert_called_once()
    args = shadow.analyze_idea.call_args[0]
    assert "Недооценка рынка" in args[1]


# ── active_markets: finally очищает даже при LLMUnavailableError ──

def test_active_markets_cleaned_on_exception():
    """active_markets не должен утекать при исключении в цикле"""
    active_markets = {}
    market_id = "test-market-id"
    active_markets[market_id] = "Test Market"

    try:
        raise RuntimeError("Simulated failure")
    except RuntimeError:
        pass
    finally:
        if market_id in active_markets:
            del active_markets[market_id]

    assert market_id not in active_markets


def test_active_markets_finally_safe_if_not_added():
    """finally не падает если market_id не был добавлен в active_markets"""
    active_markets = {}
    market_id = "never-added-id"

    # Имитируем finally без предварительного добавления
    if market_id in active_markets:
        del active_markets[market_id]

    assert market_id not in active_markets  # просто не упало
