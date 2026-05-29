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
    """datetime.now должен вызываться ровно один раз за весь select()."""
    mock_adapter = MagicMock()
    mock_adapter.list_markets.return_value = []
    mock_adapter.list_markets_paged.return_value = []
    mock_adapter.list_markets_ending_soon.return_value = []
    selector = MarketSelector(mock_adapter)
    
    with patch("agents.shared.python.market_selector.datetime") as mock_dt, \
         patch("agents.shared.python.market_selector.get_markets_on_cooldown", return_value=set()):
        mock_dt.now.return_value = datetime(2026, 6, 1, tzinfo=timezone.utc)
        selector.select(total_limit=5)
        # now() должен быть вызван ровно 1 раз (в select()), а не N раз внутри циклов
        assert mock_dt.now.call_count == 1
