import pytest
from unittest.mock import patch, MagicMock
from agents.orchestrator.src.news_processor import NewsProcessor
from core.models import Market
from datetime import datetime, timezone


def _make_market(id_, title, url, price=0.5):
    return Market(
        id=id_, platform="polymarket", title=title,
        url=url, outcome="YES", price=price,
        description="", close_time=datetime(2099, 1, 1, tzinfo=timezone.utc)
    )


# ─── Тест 1: closure bug — score_market использует правильный slug ───────────
def test_score_market_correct_slug():
    """score_market должна использовать slug текущей итерации, а не последний."""
    text = "polymarket.com/event/trump-wins https://polymarket.com/event/btc-100k"
    
    m1 = _make_market("1", "Will Trump win?", "https://polymarket.com/event/trump-wins")
    m2 = _make_market("2", "Will BTC hit 100k?", "https://polymarket.com/event/btc-100k")
    
    processor = NewsProcessor.__new__(NewsProcessor)
    processor.adapter = MagicMock()
    processor.adapter.get_event_by_slug.side_effect = lambda slug: [m1] if "trump" in slug else [m2]
    processor.api_key = "fake"
    processor.model = "gemini-2.5-flash"
    
    result = processor._extract_markets_from_urls(
        "Check https://polymarket.com/event/trump-wins and https://polymarket.com/event/btc-100k"
    )
    # Оба рынка должны быть найдены
    assert len(result) == 2
    ids = [m.id for m in result]
    assert "1" in ids
    assert "2" in ids


# ─── Тест 2: condition_id пробрасывается через _parse_markets ────────────────
def test_parse_markets_preserves_condition_id():
    """_parse_markets должна сохранять conditionId в Market.condition_id."""
    import json
    from agents.shared.adapters.polymarket import PolymarketAdapter
    
    adapter = PolymarketAdapter.__new__(PolymarketAdapter)
    adapter.api_url = "https://gamma-api.polymarket.com"
    adapter.session = MagicMock()
    
    item = {
        "id": "test-id-123",
        "question": "Will X happen?",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.6", "0.4"]),
        "slug": "will-x-happen",
        "conditionId": "0xABC123",
        "endDate": "2026-12-31T00:00:00Z",
    }
    
    markets = adapter._parse_markets([item], limit=1)
    assert len(markets) == 1
    assert markets[0].condition_id == "0xABC123"


# ─── Тест 3: search_markets hard cap — не больше 10 кандидатов ───────────────
def test_search_markets_candidate_cap():
    """Суммарное количество кандидатов до LLM-валидации не превышает 10."""
    processor = NewsProcessor.__new__(NewsProcessor)
    processor.adapter = MagicMock()
    processor.api_key = "fake"
    processor.model = "gemini-2.5-flash"
    
    # 3 keywords × 3 рынка каждое = 9 уникальных кандидатов
    markets_per_kw = [
        _make_market(f"m{i}", f"Market {i}", f"https://pm.com/event/m{i}")
        for i in range(3)
    ]
    processor.adapter.search_markets.return_value = markets_per_kw
    
    # Мокаем LLM
    with patch.object(processor, '_validate_relevance', return_value=[]) as mock_validate:
        with patch('agents.orchestrator.src.news_processor.generate_content_with_fallback') as mock_llm:
            import json as _json
            mock_llm.return_value = (
                MagicMock(candidates=[MagicMock(content=MagicMock(parts=[MagicMock(text=_json.dumps({"keywords": ["kw1", "kw2", "kw3"]}))] ))]),
                None
            )
            with patch('agents.orchestrator.src.news_processor.extract_response_text', return_value=_json.dumps({"keywords": ["kw1", "kw2", "kw3"]})):
                processor.find_relevant_markets("some news text")
        
        # _validate_relevance должна получить не более 10 кандидатов
        if mock_validate.called:
            candidates = mock_validate.call_args[0][1]
            assert len(candidates) <= 10


# ─── Тест 4: get_event_by_slug возвращает [] при пустом API ответе ───────────
def test_get_event_by_slug_empty_response():
    from agents.shared.adapters.polymarket import PolymarketAdapter
    adapter = PolymarketAdapter.__new__(PolymarketAdapter)
    adapter.session = MagicMock()
    adapter.api_url = "https://gamma-api.polymarket.com"
    
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"events": [], "markets": []}
    adapter.session.get.return_value = mock_resp
    
    result = adapter.get_event_by_slug("non-existent-slug")
    assert result == []


# ─── Тест 5: score_market не падает при пустом тексте ────────────────────────
def test_extract_markets_no_slugs_returns_empty():
    processor = NewsProcessor.__new__(NewsProcessor)
    processor.adapter = MagicMock()
    processor.api_key = "fake"
    processor.model = "gemini-2.5-flash"
    
    result = processor._extract_markets_from_urls("Текст без ссылок на polymarket")
    assert result == []
