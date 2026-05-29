"""
Тесты для BUG-1 (offset-naive vs aware) и BUG-2 (empty parts) из production-логов.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════
# BUG-1: offset-naive vs offset-aware datetime
# ══════════════════════════════════════════════════════════════

def make_market(close_time):
    m = MagicMock()
    m.id = "test-123"
    m.title = "Test Market"
    m.description = "Test"
    m.price = 0.14
    m.platform = "polymarket"
    m.close_time = close_time
    return m


def make_context(market):
    ctx = MagicMock()
    ctx.market = market
    ctx.news_titles = []
    ctx.reddit_posts = []
    ctx.wiki_context = None
    ctx.trends_data = "0"
    ctx.hn_posts = []
    ctx.search_query = "test"
    return ctx


def run_estimate(close_time_value, news_titles=None):
    """Запускает estimate_market с замоканным LLM и возвращает результат или исключение."""
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market(close_time_value)
    context = make_context(market)
    if news_titles is not None:
        context.news_titles = news_titles

    agent = SwingAgent(api_key="test_key")

    mock_result = {
        "candidates": [{"content": {"parts": [{"text": '{"hype_potential":0.3,"recommendation":"ignore","target_outcome":"YES","target_exit_price":0.20,"confidence":0.5,"reasoning":"тест","catalyst":"нет","catalyst_absence_reason":"тест","swing_risk":"тест","swing_verdict":"тест 0.20"}'}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               return_value=(mock_result, "gemini-2.5-flash")), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        return agent.estimate_market(context, price_history=[])


def test_close_time_aware_utc_does_not_raise():
    """
    close_time с tzinfo=UTC (типично для Polymarket API) не должен вызывать TypeError.
    BUG: datetime.utcnow() (naive) - close_time (aware) → TypeError.
    FIX: использовать datetime.now(tz=timezone.utc) для получения текущего времени.
    """
    close_aware = datetime.now(tz=timezone.utc) + timedelta(days=30)
    try:
        run_estimate(close_aware)
        # Либо Signal, либо None — оба приемлемы, главное без TypeError
    except TypeError as e:
        pytest.fail(
            f"TypeError при aware close_time: {e}\n"
            f"BUG-1: 'can't subtract offset-naive and offset-aware datetimes'\n"
            f"FIX: заменить datetime.utcnow() на datetime.now(tz=timezone.utc)"
        )


def test_close_time_naive_does_not_raise():
    """close_time без tzinfo (legacy/DB) тоже должен работать без TypeError."""
    close_naive = datetime.utcnow() + timedelta(days=30)
    try:
        run_estimate(close_naive)
    except TypeError as e:
        pytest.fail(f"TypeError при naive close_time: {e}")


def test_close_time_plus3_timezone_does_not_raise():
    """close_time с произвольным timezone (UTC+3) должен работать корректно."""
    close_plus3 = datetime.now(tz=timezone(timedelta(hours=3))) + timedelta(days=30)
    try:
        run_estimate(close_plus3)
    except TypeError as e:
        pytest.fail(f"TypeError при UTC+3 close_time: {e}")


def test_hours_to_close_positive_for_future_market():
    """hours_to_close должен быть положительным для рынка с датой закрытия в будущем."""
    # Патчим расчёт hype чтобы проверить только hours_to_close
    close_aware = datetime.now(tz=timezone.utc) + timedelta(hours=72)
    captured = {}

    original_calc = None
    try:
        from agents.shared.utils.hype_calculator import calculate_hype_potential, HypeMetrics
        original_calc = calculate_hype_potential

        def capturing_calc(metrics: HypeMetrics):
            captured["hours_to_close"] = metrics.hours_to_close
            return original_calc(metrics)

        with patch("agents.shared.utils.hype_calculator.calculate_hype_potential",
                   side_effect=capturing_calc):
            run_estimate(close_aware)

        assert "hours_to_close" in captured, "calculate_hype_potential не была вызвана"
        assert captured["hours_to_close"] > 0, (
            f"hours_to_close={captured['hours_to_close']:.2f} не положительный для рынка через 72ч.\n"
            f"Вероятная причина: неправильное вычитание aware и naive datetime."
        )
        assert captured["hours_to_close"] < 200, (
            f"hours_to_close={captured['hours_to_close']:.2f} слишком большой — ошибка вычисления."
        )
    except ImportError:
        pytest.skip("hype_calculator не найден")


def test_aware_news_publication_date_does_not_raise():
    """
    Новость с aware-датой публикации (содержит '+00:00') не должна вызывать TypeError
    при подсчёте recent_news_count.
    BUG: now = datetime.utcnow() (naive) - pub_dt с tzinfo → TypeError.
    FIX: now = datetime.now(tz=timezone.utc) + нормализация pub_dt.
    """
    close_aware = datetime.now(tz=timezone.utc) + timedelta(days=10)
    # Новость с aware-датой (ISO 8601 с timezone)
    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    news_with_aware_date = [
        f"[{now_iso}+00:00] Latest MetaMask token news"
    ]
    try:
        run_estimate(close_aware, news_titles=news_with_aware_date)
    except TypeError as e:
        pytest.fail(
            f"TypeError при aware publication date: {e}\n"
            f"BUG-1b: (datetime.utcnow() - aware pub_dt) → TypeError\n"
            f"FIX: now = datetime.now(tz=timezone.utc); нормализуй pub_dt"
        )


def test_mixed_naive_aware_news_does_not_raise():
    """Микс naive и aware дат в новостях не должен вызывать TypeError."""
    close_aware = datetime.now(tz=timezone.utc) + timedelta(days=10)
    news_mixed = [
        "[2026-05-29 08:00:00] Naive date news",
        "[2026-05-29 08:00:00+00:00] Aware date news",
        "[дата неизвестна] Unknown date news",
        "No date brackets at all"
    ]
    try:
        run_estimate(close_aware, news_mixed)
    except TypeError as e:
        pytest.fail(f"TypeError при смешанных датах новостей: {e}")


# ══════════════════════════════════════════════════════════════
# BUG-2: extract_response_text при parts = []
# ══════════════════════════════════════════════════════════════

def test_extract_response_text_empty_parts_returns_empty_string():
    """
    extract_response_text при parts=[] должна вернуть "" вместо ValueError.
    BUG: result['candidates'][0]['content']['parts'][0] → IndexError → ValueError.
    FIX: if not parts: return "".
    """
    from agents.shared.utils.gemini_client import extract_response_text

    result_empty_parts = {
        "candidates": [{"content": {"parts": [], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 406, "candidatesTokenCount": 15}
    }

    try:
        text = extract_response_text(result_empty_parts)
        assert text == "", (
            f"Ожидалась пустая строка для parts=[], получено: {repr(text)}"
        )
    except ValueError as e:
        pytest.fail(
            f"extract_response_text бросила ValueError при parts=[]: {e}\n"
            f"BUG-2: пустые parts — не ошибка, а валидный ответ (content_filter или tool-only).\n"
            f"FIX: добавить 'if not parts: return \"\"' перед обращением к parts[0]."
        )


def test_extract_response_text_normal_case_still_works():
    """Регрессия: нормальный ответ с текстом продолжает работать."""
    from agents.shared.utils.gemini_client import extract_response_text

    result_ok = {
        "candidates": [{"content": {"parts": [{"text": "Привет, мир!"}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    }
    assert extract_response_text(result_ok) == "Привет, мир!"


def test_extract_response_text_missing_candidates_raises():
    """При полностью некорректном ответе (нет candidates) ValueError должна бросаться."""
    from agents.shared.utils.gemini_client import extract_response_text

    with pytest.raises(ValueError):
        extract_response_text({"error": "model_error"})


def test_convert_openai_to_gemini_empty_content_produces_empty_part():
    """
    convert_openai_to_gemini при пустом content и без tool_calls
    должна добавлять parts=[{"text": ""}] а не parts=[].
    """
    from agents.shared.utils.gemini_client import convert_openai_to_gemini

    openai_res = {
        "choices": [{
            "message": {"content": None, "tool_calls": None},
            "finish_reason": "content_filter"
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 0}
    }

    result = convert_openai_to_gemini(openai_res)
    parts = result["candidates"][0]["content"]["parts"]
    assert len(parts) >= 1, (
        f"convert_openai_to_gemini вернула parts=[] при пустом content.\n"
        f"BUG-2: extract_response_text упадёт с IndexError.\n"
        f"FIX: гарантировать минимум parts=[{{\"text\": \"\"}}] когда parts пустой."
    )


# ══════════════════════════════════════════════════════════════
# Интеграционный тест: оба бага вместе
# ══════════════════════════════════════════════════════════════

def test_swing_agent_survives_aware_datetime_and_empty_parts():
    """
    SwingAgent с aware close_time и OpenRouter-ответом с parts=[]
    должен корректно перейти на следующий провайдер, а не крэшиться.
    """
    from agents.polymarket_swing_agent.src.agent import SwingAgent

    market = make_market(datetime.now(tz=timezone.utc) + timedelta(days=15))
    context = make_context(market)

    agent = SwingAgent(api_key="test_key")

    # Первый вызов: пустые parts (имитация OpenRouter с content_filter)
    empty_parts_result = {
        "candidates": [{"content": {"parts": [], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 406, "candidatesTokenCount": 0}
    }
    # Второй вызов: нормальный ответ
    ok_result = {
        "candidates": [{"content": {"parts": [{"text": '{"hype_potential":0.2,"recommendation":"ignore","target_outcome":"YES","target_exit_price":0.18,"confidence":0.4,"reasoning":"тест","catalyst":"нет","catalyst_absence_reason":"тест","swing_risk":"минимальный","swing_verdict":"тест 0.18"}'}], "role": "model"}}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}
    }

    call_count = {"n": 0}
    def mock_generate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return empty_parts_result, "openrouter"
        return ok_result, "gemini-2.5-flash"

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               side_effect=mock_generate), \
         patch("agents.polymarket_swing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_swing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.shared.utils.rag.get_rag_context", return_value=""), \
         patch("agents.shared.utils.prompt_guards.guard_news_with_age", return_value=""), \
         patch("agents.shared.utils.language_guard.validate_russian_fields", return_value=None):
        try:
            result = agent.estimate_market(context, price_history=[])
        except TypeError as e:
            pytest.fail(f"TypeError выжил в интеграционном тесте: {e}")
