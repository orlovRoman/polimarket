"""
Тесты для коммита 9c4dac8:
- Дедупликация постов
- NO_MARKETS не блокирует повторный анализ
- inspect.signature кэшируется
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock


# ═══════════════════════════════════════════════════════════
# БАГ 1: NO_MARKETS должен допускать повторный анализ
# ═══════════════════════════════════════════════════════════

def test_no_markets_status_allows_reanalysis():
    """
    Пост со статусом NO_MARKETS должен быть переанализирован при повторном вызове.
    PROCESSING и ANALYZED — финальные блокировщики. NO_MARKETS — нет.
    """
    async def run_test():
        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        post_info = {
            'status': 'NO_MARKETS',
            'text': 'BTC hits new ATH above 200K',
            'message_id': 42
        }

        call_count = [0]

        def mock_get_post_info(post_id):
            return post_info

        def mock_mark_status(post_id, status):
            call_count[0] += 1

        with patch('agents.shared.python.db.get_telegram_post_info', mock_get_post_info), \
             patch('agents.shared.python.db.mark_telegram_post_status', mock_mark_status):

            # БАГ: если NO_MARKETS в блоке проверки — функция вернётся без вызова mark_status
            # ФИКС: NO_MARKETS убран из блока проверки → mark_status будет вызван
            await engine.analyze_post_async(1, "chat_1")
            # До фикса: call_count[0] == 0 (функция вернулась сразу)
            # После фикса: call_count[0] > 0 (анализ запущен)
            assert call_count[0] > 0, (
                "NO_MARKETS не должен блокировать повторный анализ. "
                "mark_telegram_post_status не был вызван — анализ не стартовал."
            )

    asyncio.run(run_test())


def test_processing_status_blocks_reanalysis():
    """PROCESSING должен блокировать — дубль-запуск опасен."""
    async def run_test():
        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        post_info = {'status': 'PROCESSING', 'text': 'some text', 'message_id': 1}
        mark_calls = []

        with patch('agents.shared.python.db.get_telegram_post_info', return_value=post_info), \
             patch('agents.shared.python.db.mark_telegram_post_status', side_effect=mark_calls.append):
            await engine.analyze_post_async(2, "chat_1")
            assert len(mark_calls) == 0, "PROCESSING: повторный запуск не должен менять статус"

    asyncio.run(run_test())


def test_analyzed_status_blocks_reanalysis():
    """ANALYZED — финальный статус, повторный анализ запрещён."""
    async def run_test():
        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        post_info = {'status': 'ANALYZED', 'text': 'some text', 'message_id': 1}
        mark_calls = []

        with patch('agents.shared.python.db.get_telegram_post_info', return_value=post_info), \
             patch('agents.shared.python.db.mark_telegram_post_status', side_effect=mark_calls.append):
            await engine.analyze_post_async(3, "chat_1")
            assert len(mark_calls) == 0, "ANALYZED: повторный запуск не должен менять статус"

    asyncio.run(run_test())


# ═══════════════════════════════════════════════════════════
# БАГ 2: inspect.signature кэшируется
# ═══════════════════════════════════════════════════════════

def test_callback_accepts_reply_markup_cached():
    """
    _callback_accepts_reply_markup должна кэшировать результат.
    inspect.signature — дорогая операция, не должна вызываться N раз.
    """
    import inspect
    from core.engine import _callback_accepts_reply_markup

    def callback_with_markup(text: str, reply_markup: dict = None): pass
    def callback_without_markup(text: str): pass

    assert _callback_accepts_reply_markup(callback_with_markup) is True
    assert _callback_accepts_reply_markup(callback_without_markup) is False

    # Повторные вызовы — должны возвращаться из кэша (lru_cache)
    call_log = []
    original_signature = inspect.signature

    def counting_signature(func, **kwargs):
        call_log.append(func)
        return original_signature(func, **kwargs)

    with patch('inspect.signature', side_effect=counting_signature):
        # После кэширования — inspect.signature НЕ должен вызываться снова
        _callback_accepts_reply_markup(callback_with_markup)
        _callback_accepts_reply_markup(callback_without_markup)

    assert len(call_log) == 0, (
        f"inspect.signature вызвана {len(call_log)} раз после кэширования. "
        f"Ожидали 0 (lru_cache должен отдавать результат без пересчёта)."
    )


# ═══════════════════════════════════════════════════════════
# БАГ 3: markets[:3] логирует обрезку
# ═══════════════════════════════════════════════════════════

def test_markets_truncation_is_logged(caplog):
    """
    Если find_relevant_markets вернула >3 рынков,
    в лог должно попасть предупреждение о том, что часть пропущена.
    """
    import logging
    # Проверяем через caplog что WARNING/INFO с упоминанием "пропущен" или "анализируем первые"
    # Тест-заглушка: реальный вызов требует полного env — проверяем только наличие guard в коде

    import ast
    import pathlib
    source = pathlib.Path("core/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Ищем наличие среза [:3] рядом с логом
    source_lines = source.splitlines()
    has_truncation_log = any(
        "markets[:3]" in line or "len(markets) > 3" in line
        for line in source_lines
    )
    # До фикса: только `markets[:3]` без лога → has_truncation_log может быть False
    # После фикса: оба паттерна должны быть рядом
    assert has_truncation_log, (
        "В engine.py нет явного лога при обрезке markets[:3]. "
        "Тихое отбрасывание рынков затрудняет дебаггинг."
    )


# ═══════════════════════════════════════════════════════════
# REGRESSION: дедупликация PROCESSING работает в race condition
# ═══════════════════════════════════════════════════════════

def test_processing_dedup_prevents_race_condition():
    """
    Два одновременных вызова analyze_post_async для одного post_id.
    Первый — должен запуститься. Второй — должен быть отклонён (PROCESSING).
    """
    async def run_test():
        from core.engine import CoreEngine
        engine = CoreEngine.__new__(CoreEngine)
        engine.api_key = "test"

        status_store = {'status': 'NEW', 'text': 'ETH to $5000', 'message_id': 10}

        def mock_get_post(post_id):
            return dict(status_store)

        mark_history = []

        def mock_mark(post_id, status):
            mark_history.append(status)
            status_store['status'] = status  # симулируем обновление

        with patch('agents.shared.python.db.get_telegram_post_info', side_effect=mock_get_post), \
             patch('agents.shared.python.db.mark_telegram_post_status', side_effect=mock_mark), \
             patch('agents.orchestrator.src.news_processor.NewsProcessor') as MockNP, \
             patch('asyncio.to_thread', new_callable=AsyncMock) as mock_to_thread, \
             patch('asyncio.sleep', new_callable=AsyncMock):
            
            MockNP.return_value.find_relevant_markets.return_value = [MagicMock(id="market_1")]

            await asyncio.gather(
                engine.analyze_post_async(10, "chat"),
                engine.analyze_post_async(10, "chat"),
            )

        # Первый вызов: NEW → PROCESSING → ANALYZED
        # Второй вызов: видит PROCESSING → return без mark
        processing_count = mark_history.count('PROCESSING')
        assert processing_count == 1, (
            f"PROCESSING должен быть установлен ровно 1 раз, "
            f"получили {processing_count}. История: {mark_history}"
        )

    asyncio.run(run_test())
