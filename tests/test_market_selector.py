import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from core.models import Market
from agents.shared.python.market_selector import MarketSelector
from agents.shared.utils.gemini_client import generate_content_with_fallback

def test_ending_soon_not_filtered():
    """Рынок, закрывающийся через 6ч, должен пройти фильтр ending_soon."""
    mock_market = Market(
        id="test_ending",
        platform="polymarket",
        title="Will Rihanna release a new album?",
        url="http://url",
        outcome="YES",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(hours=6)
    )
    mock_adapter = MagicMock()
    selector = MarketSelector(mock_adapter)
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        filtered = selector._filter([mock_market], min_hours=1)
    assert len(filtered) == 1, "ending_soon рынок должен пройти фильтр"

def test_regular_short_market_filtered():
    """Рынок через 6ч должен отфильтроваться при стандартном скане."""
    mock_market = Market(
        id="test_regular",
        platform="polymarket",
        title="Will Rihanna release a new album?",
        url="http://url",
        outcome="YES",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(hours=6)
    )
    mock_adapter = MagicMock()
    selector = MarketSelector(mock_adapter)
    
    with patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        filtered = selector._filter([mock_market], min_hours=12)
    assert len(filtered) == 0, "Рынок < 12h должен отфильтроваться"

def test_gemini_model_not_sent_to_openrouter():
    """Gemini-модель в БД-конфиге для OpenRouter должна переключаться на OpenRouter дефолт."""
    with patch("agents.shared.python.db.get_memory") as mock_memory, \
         patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG") as mock_providers_config:
         
        mock_result = {"candidates": [{"content": {"parts": [{"text": "dummy"}]}}]}
        mock_send_gemini = MagicMock(return_value=(mock_result, 0, 0))
        mock_send_openrouter = MagicMock(return_value=(mock_result, 0, 0))
        
        mock_providers_config.__getitem__.side_effect = lambda k: {
            "gemini": {
                "keys": ["key1"],
                "models": ["gemini-2.5-flash"],
                "send_func": mock_send_gemini
            },
            "openrouter": {
                "keys": ["or_key"],
                "models": ["meta-llama/llama-3.3-70b-instruct:free"],
                "send_func": mock_send_openrouter
            },
            "cerebras": {
                "keys": [],
                "models": [],
                "send_func": MagicMock()
            }
        }[k]
        
        mock_memory.return_value = {"provider": "openrouter", "model": "gemini-2.5-flash"}
        
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "or_key", "OPENROUTER_MODEL": "meta-llama/llama-3.3-70b-instruct:free"}):
            _, _ = generate_content_with_fallback(
                api_key="gemini_key",
                payload={"contents": [{"parts": [{"text": "hello"}]}]},
                default_model="gemini-2.5-flash",
                agent_name="scout",
                timeout=30
            )
            
            mock_send_openrouter.assert_called_once()
            called_model = mock_send_openrouter.call_args[0][1]
            assert "gemini" not in called_model
            assert called_model == "meta-llama/llama-3.3-70b-instruct:free"

def test_dynamic_timeouts():
    """Проверяем, что тайм-аут регулируется в зависимости от выбранной модели."""
    with patch("agents.shared.python.db.get_memory", return_value=None), \
         patch("agents.shared.utils.gemini_client.PROVIDERS_CONFIG") as mock_providers_config:
         
        mock_result = {"candidates": [{"content": {"parts": [{"text": "dummy"}]}}]}
        mock_send_gemini = MagicMock(return_value=(mock_result, 0, 0))
        
        mock_providers_config.__getitem__.side_effect = lambda k: {
            "gemini": {
                "keys": ["key1"],
                "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
                "send_func": mock_send_gemini
            },
            "openrouter": {
                "keys": [],
                "models": [],
                "send_func": MagicMock()
            },
            "cerebras": {
                "keys": [],
                "models": [],
                "send_func": MagicMock()
            }
        }[k]
        
        # 1. Проверяем Pro
        generate_content_with_fallback(
            api_key="gemini_key",
            payload={"contents": [{"parts": [{"text": "hello"}]}]},
            default_model="gemini-2.5-pro",
            agent_name="scout",
            timeout=30
        )
        assert mock_send_gemini.call_args[0][3] == 90
        
        # 2. Проверяем Flash
        mock_send_gemini.reset_mock()
        generate_content_with_fallback(
            api_key="gemini_key",
            payload={"contents": [{"parts": [{"text": "hello"}]}]},
            default_model="gemini-2.5-flash",
            agent_name="scout",
            timeout=30
        )
        assert mock_send_gemini.call_args[0][3] == 45

def test_score_market_uses_provided_now():
    """datetime.now не должен вызываться в циклах (вызывается <= 2 раз за весь select())."""
    mock_adapter = MagicMock()
    mock_adapter.list_markets.return_value = []
    mock_adapter.list_markets_paged.return_value = []
    mock_adapter.list_markets_ending_soon.return_value = []
    selector = MarketSelector(mock_adapter)
    
    with patch("agents.shared.python.market_selector.datetime") as mock_dt, \
         patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        mock_dt.now.return_value = datetime(2026, 6, 1, tzinfo=timezone.utc)
        selector.select(total_limit=5)
        # now() не должен вызываться в циклах по рынкам
        assert mock_dt.now.call_count <= 2

def test_filter_single_db_call_for_market_lists():
    """_filter должен сделать ровно 1 запрос к get_all_listed_market_ids, не N."""
    def make_market(mid):
        return Market(
            id=mid,
            platform="polymarket",
            title=f"Market {mid}",
            url="http://url",
            outcome="YES",
            price=0.5,
            close_time=datetime.now(timezone.utc) + timedelta(hours=24)
        )
    markets = [make_market(f"m{i}") for i in range(20)]
    mock_adapter = MagicMock()
    selector = MarketSelector(mock_adapter)
    with patch("agents.shared.python.market_selector.get_all_listed_market_ids") as mock_fn, \
         patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        mock_fn.return_value = {'ignored': set(), 'watching': set()}
        selector._filter(markets)
        assert mock_fn.call_count == 1  # независимо от len(markets)

def test_penny_fetch_skips_closed_markets():
    """penny_stocks fetch должен отфильтровывать закрытые и закрывающиеся в течение 1 часа рынки."""
    now = datetime.now(timezone.utc)
    closed = Market(
        id="closed",
        platform="polymarket",
        title="Closed Penny",
        url="http://url",
        outcome="YES",
        price=0.03,
        close_time=now - timedelta(hours=1)
    )
    ending_too_soon = Market(
        id="ending_too_soon",
        platform="polymarket",
        title="Ending Too Soon Penny",
        url="http://url",
        outcome="YES",
        price=0.03,
        close_time=now + timedelta(minutes=30)
    )
    active = Market(
        id="active",
        platform="polymarket",
        title="Active Penny",
        url="http://url",
        outcome="YES",
        price=0.04,
        close_time=now + timedelta(days=5)
    )
    
    mock_adapter = MagicMock()
    mock_adapter.list_markets_paged.side_effect = [
        [closed, ending_too_soon],  # offset=0
        [active]  # offset=500
    ]
    selector = MarketSelector(mock_adapter)
    result = selector._fetch_category("penny_stocks", limit=10, now=now)
    
    assert any(m.id == "active" for m in result), "Активный рынок должен быть в результате"
    assert all(m.id != "closed" for m in result), "Закрытый рынок не должен попасть в penny"
    assert all(m.id != "ending_too_soon" for m in result), "Рынок, закрывающийся менее чем через 1 час, не должен попасть в penny"

def test_filter_single_db_call_for_cooldown_prices():
    """get_last_analyzed_price не должна вызываться в цикле, а должна вызываться батчем get_last_analyzed_prices."""
    def make_market(mid):
        return Market(
            id=mid,
            platform="polymarket",
            title=f"Market {mid}",
            url="http://url",
            outcome="YES",
            price=0.5,
            close_time=datetime.now(timezone.utc) + timedelta(hours=24)
        )
    markets = [make_market(f"m{i}") for i in range(15)]
    cooldown_set = {f"m{i}" for i in range(15)}
    
    mock_adapter = MagicMock()
    selector = MarketSelector(mock_adapter)
    
    with patch("agents.shared.python.market_selector.get_last_analyzed_prices") as mock_bulk, \
         patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=cooldown_set), \
         patch("agents.shared.python.market_selector.get_all_listed_market_ids", return_value={'ignored': set(), 'watching': set()}):
        mock_bulk.return_value = {f"m{i}": 0.5 for i in range(15)}
        selector._filter(markets)
        assert mock_bulk.call_count == 1  # один батч-запрос, не 15

def test_penny_fetch_respects_min_hours():
    """Рынок с 6ч до закрытия не должен войти в penny при min_hours=12, но должен войти при min_hours=1."""
    now = datetime.now(timezone.utc)
    m_6h = Market(
        id="short",
        platform="polymarket",
        title="Short Penny",
        url="http://url",
        outcome="YES",
        price=0.03,
        close_time=now + timedelta(hours=6)
    )
    m_48h = Market(
        id="long",
        platform="polymarket",
        title="Long Penny",
        url="http://url",
        outcome="YES",
        price=0.03,
        close_time=now + timedelta(hours=48)
    )
    
    mock_adapter = MagicMock()
    mock_adapter.list_markets_paged.side_effect = [
        [m_6h, m_48h],  # offset=0
        []  # offset=500
    ]
    selector = MarketSelector(mock_adapter)
    
    # 1. При min_hours=12: m_6h отфильтровывается
    result = selector._fetch_category("penny_stocks", limit=10, now=now, min_hours=12)
    ids = [m.id for m in result]
    assert "short" not in ids
    assert "long" in ids

    # Reset side effect for second call
    mock_adapter.list_markets_paged.side_effect = [
        [m_6h, m_48h],
        []
    ]
    # 2. При min_hours=1: m_6h сохраняется
    result_short = selector._fetch_category("penny_stocks", limit=10, now=now, min_hours=1)
    ids_short = [m.id for m in result_short]
    assert "short" in ids_short
    assert "long" in ids_short
