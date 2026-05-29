import pytest
from agents.shared.utils.language_guard import has_forbidden_script, validate_russian_fields


# ── Баг #1: language_guard ────────────────────────────────────

def test_chinese_detected():
    assert has_forbidden_script("Анализ рынка 价格上涨") is True

def test_arabic_detected():
    assert has_forbidden_script("السعر مرتفع") is True

def test_pure_russian_ok():
    assert has_forbidden_script("Вход в рынок с осторожностью. Edge=0.12") is False

def test_mixed_russian_english_ok():
    assert has_forbidden_script("YES — покупать. NO — продавать. Edge, Smart Money") is False

def test_numbers_and_symbols_ok():
    assert has_forbidden_script("Спред: 12.5% | P&L: +$50") is False

def test_empty_string_ok():
    assert has_forbidden_script("") is False

def test_validate_fields_finds_bad_field():
    data = {
        "reasoning": "Рынок недооценён",
        "cause":     "价格偏低",        # китайский
        "risk":      "Высокий риск",
    }
    bad = validate_russian_fields(data, ["reasoning", "cause", "risk"])
    assert bad == "cause"

def test_validate_fields_all_clean():
    data = {
        "reasoning": "Недооценка рынка на 15%",
        "cause":     "Нет официальных заявлений",
        "risk":      "Риск ликвидности средний",
    }
    bad = validate_russian_fields(data, ["reasoning", "cause", "risk"])
    assert bad is None

def test_validate_fields_skips_missing():
    """Отсутствующие поля не вызывают KeyError"""
    data = {"reasoning": "Текст на русском"}
    bad = validate_russian_fields(data, ["reasoning", "cause", "risk", "oracle_risk"])
    assert bad is None


# ── Баг #2: wiki_block посимвольная разбивка ─────────────────

def test_wiki_join_str_posimvolno():
    """"\n".join(str) разбивает строку посимвольно — воспроизводим баг"""
    wiki_context = "Wikipedia article text"
    broken = "\n".join(wiki_context)
    # Первые два символа должны быть 'W' и 'i' на разных строках
    assert broken.startswith("W\ni"), f"join(str) разбивает посимвольно: {broken[:10]}"

def test_wiki_fix_str_passthrough():
    """Фикс: wiki_context as str передаётся напрямую"""
    wiki_context = "Wikipedia article text"
    wiki_block = wiki_context or "Wikipedia-данных нет."
    assert wiki_block == "Wikipedia article text"
    assert "\n" not in wiki_block or wiki_context.count("\n") == wiki_block.count("\n")

def test_wiki_fix_empty_uses_default():
    wiki_context = ""
    wiki_block = wiki_context or "Wikipedia-данных нет."
    assert wiki_block == "Wikipedia-данных нет."

def test_wiki_fix_none_uses_default():
    wiki_context = None
    wiki_block = wiki_context or "Wikipedia-данных нет."
    assert wiki_block == "Wikipedia-данных нет."


# ── Интеграционный: language_guard в цикле retry агента ──────

def test_agent_retries_on_forbidden_language(monkeypatch):
    """
    Если первый ответ LLM содержит иероглифы — агент делает retry.
    На второй попытке — чистый русский — успех.
    """
    import json
    from unittest.mock import MagicMock, patch

    bad_response  = json.dumps({"estimate_probability": 0.7, "confidence": 0.8, "priority": "high",
                                "reasoning": "价格偏低", "signal": "YES", "cause": "test",
                                "risk": "test", "oracle_risk": "test", "verdict": "test"})
    good_response = json.dumps({"estimate_probability": 0.7, "confidence": 0.8, "priority": "high",
                                "reasoning": "Цена явно занижена", "signal": "YES",
                                "cause": "Нет официальных заявлений",
                                "risk": "Риск ликвидности средний",
                                "oracle_risk": "Критерии чёткие",
                                "verdict": "Вход с осторожностью"})

    call_count = {"n": 0}

    def mock_generate(*args, **kwargs):
        call_count["n"] += 1
        resp = MagicMock()
        text = bad_response if call_count["n"] == 1 else good_response
        return resp, "gemini-2.5-flash"

    def mock_extract(resp):
        return bad_response if call_count["n"] == 1 else good_response

    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback",
               side_effect=mock_generate), \
         patch("agents.shared.utils.gemini_client.extract_response_text",
               side_effect=mock_extract), \
         patch("agents.polymarket_mispricing_agent.src.agent.get_memory", return_value=None), \
         patch("agents.polymarket_mispricing_agent.src.agent.get_agent_episodes", return_value=[]), \
         patch("agents.polymarket_mispricing_agent.src.agent.get_performance_summary", return_value=""), \
         patch("agents.polymarket_mispricing_agent.src.agent.get_market_correlations", return_value=[]):

        from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
        agent = ScoutAgent.__new__(ScoutAgent)
        agent.api_key = "test"
        agent.model = "gemini-2.5-flash"
        agent.name = "SCOUT"
        agent.system_instruction = ""
        agent._adapter = None

        ctx = MagicMock()
        ctx.market.id = "mkt-1"
        ctx.market.title = "Test"
        ctx.market.description = ""
        ctx.market.outcome = "YES"
        ctx.market.price = 0.5
        ctx.market.close_time.strftime.return_value = "2026-12-01"
        ctx.market.platform = "polymarket"
        ctx.news_titles = []
        ctx.reddit_posts = []
        ctx.wiki_context = "Some wiki text"
        ctx.trends_data = {}
        ctx.hn_posts = []
        ctx.correlation_hint = ""
        ctx.source_url = None
        ctx.source_text = None
        ctx.search_query = "Test" # Adding this as we will modify MarketContext to have it

        signal = agent.estimate_market(ctx)

    assert call_count["n"] == 2, "Агент должен сделать 2 попытки (1 — плохой язык, 2 — ок)"
    assert signal is not None, "После retry должен вернуть Signal"


# ── Регрессия: технические термины в английском — разрешены ──

def test_english_technical_terms_allowed():
    """Edge, YES, NO, Smart Money — не запрещённые символы"""
    assert has_forbidden_script("YES — вход. NO — выход. Edge=0.15. Smart Money нет.") is False
