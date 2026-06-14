import pytest
from unittest.mock import patch, MagicMock
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw

def test_heuristic_filters_mutually_exclusive():
    # Создаем мок API-ответа для двух событий: одно накопительное, одно взаимоисключающее
    
    # Событие 1: взаимоисключающее (mutually exclusive)
    # Например: Кто выиграет выборы. Сумма вероятностей примерно 1.0
    mutually_exclusive_event = {
        "slug": "election-winner",
        "title": "Election Winner",
        "markets": [
            {"id": "1", "question": "Candidate A above $1000", "outcomePrices": '["0.6", "0.4"]', "volume": "10000"},
            {"id": "2", "question": "Candidate B above $1000", "outcomePrices": '["0.3", "0.7"]', "volume": "10000"},
            {"id": "3", "question": "Candidate C above $1000", "outcomePrices": '["0.1", "0.9"]', "volume": "10000"},
        ]
    }
    # Сумма price_yes = 0.6 + 0.3 + 0.1 = 1.0 < 1.2 -> Должно отфильтроваться
    
    # Событие 2: накопительное (cumulative)
    # Например: Достигнет ли цена BTC порога X, Y, Z
    cumulative_event = {
        "slug": "btc-price",
        "title": "BTC Price",
        "markets": [
            {"id": "4", "question": "BTC above $1000", "outcomePrices": '["0.9", "0.1"]', "volume": "10000"},
            {"id": "5", "question": "BTC above $2500", "outcomePrices": '["0.7", "0.3"]', "volume": "10000"},
            {"id": "6", "question": "BTC above $3000", "outcomePrices": '["0.4", "0.6"]', "volume": "10000"},
        ]
    }
    # Сумма price_yes = 0.9 + 0.7 + 0.4 = 2.0 >= 1.2 -> Должно пройти
    
    mock_events = [mutually_exclusive_event, cumulative_event]
    
    with patch("agents.polymarket_arbitrage_agent.src.synthetic.event_loader.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_events
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        events, stats = load_events_with_levels_from_raw(mock_events, min_markets=2, min_cumulative_sum=1.005)
        
        # Должно остаться только 1 событие
        assert len(events) == 1
        assert events[0].event_slug == "btc-price"
        
        # Дополнительная проверка: markets в btc-price должно быть 3
        assert len(events[0].markets) == 3

def test_unit_normalization():
    # Событие с разными единицами: B и T. Должно быть приведено к T.
    mixed_event = {
        "slug": "mixed-units-event",
        "title": "Mixed Units Event",
        "markets": [
            {"id": "1", "question": "Will Anthropic hit $800B?", "outcomePrices": '["0.6", "0.4"]', "volume": "10000"},
            {"id": "2", "question": "Will Anthropic hit $1.2T?", "outcomePrices": '["0.5", "0.5"]', "volume": "10000"},
        ]
    }
    
    events, stats = load_events_with_levels_from_raw(
        [mixed_event],
        min_markets=2,
        min_volume_per_market=1000,
        min_cumulative_sum=1.005
    )
    
    assert len(events) == 1
    assert events[0].event_slug == "mixed-units-event"
    
    markets = events[0].markets
    assert len(markets) == 2
    
    # Проверяем сумму вероятностей YES (0.6 + 0.5 = 1.1)
    total_yes = sum(m.price_yes for m in markets)
    assert abs(total_yes - 1.1) < 1e-6
    
    # Сортируем рынки по numeric_level
    sorted_m = sorted(markets, key=lambda m: m.numeric_level)
    
    # 800B должно стать 0.8T
    assert sorted_m[0].level_unit == "T"
    assert abs(sorted_m[0].numeric_level - 0.8) < 1e-6
    
    assert sorted_m[1].level_unit == "T"
    assert abs(sorted_m[1].numeric_level - 1.2) < 1e-6


