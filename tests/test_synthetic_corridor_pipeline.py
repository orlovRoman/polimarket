import pytest
from unittest.mock import patch, MagicMock

from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import PolyEvent, OutcomeMarket
from agents.polymarket_arbitrage_agent.src.synthetic.detector import ViolationCandidate
from services.synthetic_corridor_scanner import run_synthetic_corridor_scan

@pytest.mark.asyncio
async def test_synthetic_corridor_pipeline_end_to_end(caplog):
    caplog.set_level("DEBUG")
    # Фиктивные данные для рынка, который должен пройти фильтры
    mock_events = [
        {
            "slug": "test-crypto-fdv",
            "title": "Crypto Project FDV above $X",
            "markets": [
                {"id": "1", "question": "FDV above $100M", "outcomePrices": '["0.80", "0.20"]', "volume": "50000", "clobTokenIds": '["token_1_yes", "token_1_no"]'},
                {"id": "2", "question": "FDV above $200M", "outcomePrices": '["0.90", "0.10"]', "volume": "50000", "clobTokenIds": '["token_2_yes", "token_2_no"]'}, # АНОМАЛИЯ: цена выше, хотя уровень сложнее
            ]
        }
    ]

    with patch("agents.shared.adapters.polymarket.PolymarketAdapter.fetch_raw_events", return_value=mock_events), \
         patch("services.synthetic_corridor_scanner.make_session_with_timeout") as mock_make_session:

        mock_session = MagicMock()
        mock_make_session.return_value = mock_session

        def mock_orderbook(url, *args, **kwargs):
            resp = MagicMock()
            token_id = kwargs.get("params", {}).get("token_id", "")
            if "token_1_yes" in token_id:
                resp.json.return_value = {"asks": [{"price": "0.50", "size": "100"}]}
                resp.status_code = 200
            elif "token_2_yes" in token_id:
                resp.json.return_value = {"bids": [{"price": "0.90", "size": "100"}]}
                resp.status_code = 200
            else:
                resp.status_code = 404
                resp.raise_for_status.side_effect = Exception("404 Not Found")
            return resp
            
        mock_session.get.side_effect = mock_orderbook

        found_signals = run_synthetic_corridor_scan(min_real_spread_pct=0.5)
        
        if len(found_signals) != 1:
            print("LOGS:\n", caplog.text)
        assert len(found_signals) == 1
        signal = found_signals[0]
        assert signal.event_title == "Crypto Project FDV above $X"
        assert signal.lower_level == 100.0
        assert signal.upper_level == 200.0
        # Cost = 0.50 + 0.10 = 0.60
        # Spread = 1.0 - 0.60 = 0.40 = 40.0%
        assert abs(signal.real_spread_pct - 40.0) < 0.001
