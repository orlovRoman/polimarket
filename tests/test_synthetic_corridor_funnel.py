import logging
from unittest.mock import patch, MagicMock
from services.synthetic_corridor_scanner import run_synthetic_corridor_scan, logger

def test_corridor_funnel_logs_rejection_reasons():
    """Проверяем что stats-лог появляется и содержит все поля."""
    mock_violation = MagicMock()
    mock_violation.lower = MagicMock(market_id="lower_mkt")
    mock_violation.upper = MagicMock(market_id="upper_mkt")
    
    with patch("services.synthetic_corridor_scanner.PolymarketAdapter.fetch_raw_events") as mock_events, \
         patch("services.synthetic_corridor_scanner.load_events_with_levels_from_raw") as mock_levels, \
         patch("services.synthetic_corridor_scanner.find_violations") as mock_v, \
         patch("services.synthetic_corridor_scanner.fetch_real_entry_prices") as mock_ob, \
         patch("services.synthetic_corridor_scanner.logger") as mock_logger:
        
        mock_events.return_value = []
        mock_levels.return_value = []
        mock_v.return_value = [mock_violation]
        mock_ob.return_value = {
            "real_spread_pct": 0.2, 
            "executable_size_contracts": 5,
            "ask_yes_lower": 0.3, 
            "ask_no_upper": 0.7,
            "real_cost": 1.0, 
            "depth_5_lower": 10, 
            "depth_5_upper": 10
        }
        
        run_synthetic_corridor_scan(
            min_real_spread_pct=1.5, 
            min_executable_contracts=50
        )

    log_calls = [args[0][0] for args in mock_logger.info.call_args_list]
    log_text = " ".join(log_calls)
    assert "low_spread=1" in log_text
