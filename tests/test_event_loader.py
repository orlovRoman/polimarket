import pytest
from unittest.mock import patch, MagicMock
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels

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
            {"id": "5", "question": "BTC above $2000", "outcomePrices": '["0.7", "0.3"]', "volume": "10000"},
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
        
        events = load_events_with_levels(min_markets_per_event=2)
        
        # Должно остаться только 1 событие
        assert len(events) == 1
        assert events[0].event_slug == "btc-price"
        
        # Дополнительная проверка: markets в btc-price должно быть 3
        assert len(events[0].markets) == 3
