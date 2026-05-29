# tests/test_swing_agent_v8.py
"""
Тесты для BUG-1 (empty content → json.loads) и BUG-2 (guard_news_with_age timezone).
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


PAYLOAD_BASE = {"contents": [{"parts": [{"text": "test"}], "role": "user"}]}


def make_market(close_time=None):
    m = MagicMock()
    m.id = "mkt-001"
    m.title = "Test Market"
    m.description = ""
    m.price = 0.14
    m.platform = "polymarket"
    m.close_time = close_time or datetime.now(tz=timezone.utc) + timedelta(days=30)
    return m


def make_context(market, news=None):
    ctx = MagicMock()
    ctx.market = market
    ctx.news_titles = news or []
    ctx.reddit_posts = []
    ctx.wiki_context = None
    ctx.trends_data = "0"
    ctx.hn_posts = []
    ctx.search_query = "test"
    return ctx


# ══════════════════════════════════════════════════════════════
# BUG-1: empty content → json.loads("") → JSONDecodeError
# ══════════════════════════════════════════════════════════════

def test_empty_extract_response_text_does_not_crash_agent():
    """
    Если extract_response_text возвращает "" (пустые parts),
    SwingAgent не должен падать с JSONDecodeError — должен перейти к следующей попытке.
    BUG: json.loads("") → JSONDecodeError: Expecting value.
    FIX: добавить 'if not content: continue' после strip().
    """
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    # Имитируем: первый вызов → пустой ответ, второй → нормальный
    empty_result = {
        "candidates": [{"content": {"parts": [{"text": ""}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 0}
    }
    ok_result = {
        "candidates": [{"content": {"parts": [{"text": '{"hype_potential":0.2,"recommendation":"ignore","target_outcome":"YES","target_exit_price":0.18,"confidence":0.4,"reasoning":"тест","catalyst":"нет","catalyst_absence_reason":"нет","swing_risk":"мал","swing_verdict":"тест 0.18"}'}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}
    }

    call_n = {"n": 0}
    def mock_gen(*a, **kw):
        call_n["n"] += 1
        return (empty_result if call_n["n"] == 1 else ok_result, "gemini-2.5-flash")

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               side_effect=mock_gen), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        result = agent.estimate_market(ctx, price_history=[])

    assert result is not None, (
        "SwingAgent вернул None после пустого + валидного ответа.\n"
        "BUG-1: json.loads('') бросает JSONDecodeError, агент теряет попытку.\n"
        "FIX: добавить 'if not content: continue' перед json.loads."
    )


def test_two_empty_responses_returns_none_gracefully():
    """
    Два пустых ответа подряд → None (без исключений, без падения).
    Это ожидаемое поведение: all attempts exhausted.
    """
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market()
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    empty_result = {
        "candidates": [{"content": {"parts": [{"text": ""}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 0}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(empty_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        try:
            result = agent.estimate_market(ctx, price_history=[])
        except Exception as e:
            pytest.fail(f"SwingAgent бросил исключение при двух пустых ответах: {e}")

    assert result is None, "Ожидается None при исчерпании попыток"


def test_json_loads_empty_string_raises_json_decode_error():
    """
    Регрессионный тест: подтверждаем, что json.loads("") действительно
    бросает JSONDecodeError (обоснование существования BUG-1).
    """
    import json
    with pytest.raises(json.JSONDecodeError):
        json.loads("")


def test_content_empty_check_prevents_json_decode_error():
    """
    После фикса: content.strip() == "" → continue (не вызывается json.loads).
    Симулируем логику фикса напрямую.
    """
    import json

    def process_content(content: str):
        content = content.replace("```json", "").replace("```", "").strip()
        if not content:      # ← FIX
            return None      # continue в реальном коде
        return json.loads(content)

    assert process_content("") is None
    assert process_content("```json\n```") is None
    assert process_content('{"key": "value"}') == {"key": "value"}


# ══════════════════════════════════════════════════════════════
# BUG-2: guard_news_with_age timezone mixing
# ══════════════════════════════════════════════════════════════

def test_guard_news_with_age_aware_now_naive_pub_does_not_raise():
    """
    guard_news_with_age с aware now= и naive pub датами не должна бросать TypeError.
    BUG: если внутри функции делается (aware_now - naive_pub_dt) → TypeError.
    FIX: нормализовать pub_dt через .replace(tzinfo=timezone.utc) если naive.
    """
    try:
        from agents.shared.utils.prompt_guards import guard_news_with_age
    except ImportError:
        pytest.skip("prompt_guards не найден")

    now_aware = datetime.now(tz=timezone.utc)

    # Naive ISO строки — результат datetime.strptime().isoformat() без tzinfo
    news_items = [
        {"title": "Новость 1", "published": "2026-05-29T05:00:00"},   # naive, 3ч назад
        {"title": "Новость 2", "published": "2026-05-28T08:00:00"},   # naive, ~24ч назад
        {"title": "Новость 3", "published": None},
        {"title": "Новость 4", "published": "2026-05-29T08:00:00+00:00"},  # aware UTC
    ]

    try:
        result = guard_news_with_age(news_items, now=now_aware)
        assert isinstance(result, str), "guard_news_with_age должна возвращать строку"
    except TypeError as e:
        pytest.fail(
            f"guard_news_with_age бросила TypeError: {e}\n"
            f"BUG-2: смешивание aware now и naive pub_dt.\n"
            f"FIX: нормализовать pub_dt через pub_dt.replace(tzinfo=timezone.utc) если naive."
        )


def test_guard_news_with_age_all_naive_no_raise():
    """Все даты naive + naive now= не должны вызывать ошибок."""
    try:
        from agents.shared.utils.prompt_guards import guard_news_with_age
    except ImportError:
        pytest.skip("prompt_guards не найден")

    now_naive = datetime.utcnow()
    news_items = [
        {"title": "Старая", "published": "2026-05-27T08:00:00"},
        {"title": "Свежая", "published": datetime.utcnow().isoformat()},
    ]
    try:
        guard_news_with_age(news_items, now=now_naive)
    except TypeError as e:
        pytest.fail(f"TypeError при all-naive: {e}")


def test_guard_news_with_age_all_aware_no_raise():
    """Все даты aware UTC + aware now= не должны вызывать ошибок."""
    try:
        from agents.shared.utils.prompt_guards import guard_news_with_age
    except ImportError:
        pytest.skip("prompt_guards не найден")

    now_aware = datetime.now(tz=timezone.utc)
    news_items = [
        {"title": "Свежая", "published": "2026-05-29T08:00:00+00:00"},
        {"title": "Старая", "published": "2026-05-27T08:00:00+00:00"},
    ]
    try:
        guard_news_with_age(news_items, now=now_aware)
    except TypeError as e:
        pytest.fail(f"TypeError при all-aware: {e}")


# ══════════════════════════════════════════════════════════════
# Регрессия: ранее исправленные баги ревью #7 не регрессировали
# ══════════════════════════════════════════════════════════════

def test_close_time_aware_no_type_error_regression():
    """Регрессия BUG-1 ревью #7: aware close_time не вызывает TypeError."""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    close_aware = datetime.now(tz=timezone.utc) + timedelta(days=10)
    market = make_market(close_aware)
    ctx = make_context(market)
    agent = SwingAgent(api_key="test")

    ok_result = {
        "candidates": [{"content": {"parts": [{"text": '{"hype_potential":0.1,"recommendation":"ignore","target_outcome":"YES","target_exit_price":0.15,"confidence":0.3,"reasoning":"тест","catalyst":"нет","catalyst_absence_reason":"нет","swing_risk":"мал","swing_verdict":"тест 0.15"}'}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(ok_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        try:
            agent.estimate_market(ctx, price_history=[])
        except TypeError as e:
            pytest.fail(f"Регрессия BUG-1 #7: {e}")


def test_extract_response_text_empty_parts_returns_empty_regression():
    """Регрессия BUG-2 ревью #7: extract_response_text при parts=[] возвращает ''."""
    from agents.shared.utils.gemini_client import extract_response_text

    result = extract_response_text({
        "candidates": [{"content": {"parts": [], "role": "model"}}],
        "usageMetadata": {}
    })
    assert result == "", f"Ожидалась '', получено: {repr(result)}"
