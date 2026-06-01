import pytest
import asyncio
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from core.models import Market
from core.engine import CoreEngine

def test_math_gate_loop_closes_on_exception(monkeypatch):
    """Event loop должен закрываться даже при исключении в _run_math_gate."""
    closed_loops = []
    original_new_loop = asyncio.new_event_loop

    def mock_new_loop():
        loop = original_new_loop()
        original_close = loop.close
        def tracking_close():
            closed_loops.append(True)
            original_close()
        loop.close = tracking_close
        
        # Override run_until_complete to raise an exception
        def mock_run(*args, **kwargs):
            # Close the coroutine arg to prevent RuntimeWarning
            if args and hasattr(args[0], "close"):
                args[0].close()
            raise RuntimeError("simulated crash")
        loop.run_until_complete = mock_run
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", mock_new_loop)

    dt = datetime.now(timezone.utc)
    market = Market(id="1", platform="polymarket", title="A", url="http://x", outcome="YES", price=0.5, close_time=dt)

    # Mock engine dependency functions to prevent news loading or actual agent runs
    with patch("core.engine.save_market"), \
         patch("core.engine.logger"), \
         patch("core.engine.run_screening", return_value=["1"]), \
         patch("core.engine.get_last_analyzed_price", side_effect=ValueError("stop test")):
        
        # Reset CoreEngine singleton instance
        CoreEngine._instance = None
        engine = CoreEngine()
        engine.api_key = "fake_key"
        engine.adapter = MagicMock()
        engine.adapter.get_market.return_value = market
        
        # Call the inner discussion function
        engine._run_team_discussion_inner(
            market_id="1"
        )
        
    assert len(closed_loops) == 1
