import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_manual_trigger_no_signal_publishes_summary_only():
    from core.models import Market
    from core.context import MarketContext
    
    market = Market(
        id="mkt_manual",
        platform="polymarket",
        title="Test Manual Trigger",
        url="https://polymarket.com/event/test",
        outcome="YES",
        price=0.5,
        volume_2h=10000,
        volume_24h=50000,
        close_time="2026-12-31T23:59:59Z"
    )
    
    def mock_get_memory(*args, **kwargs):
        key = args[0] if args else kwargs.get('key')
        if key and "min_edge" in key:
            return "0.05"
        return {}
        
    context = MarketContext(market=market, trigger_type="manual")
    with patch('core.workflow.get_memory', side_effect=mock_get_memory), \
         patch('core.workflow.save_signal') as mock_save_signal, \
         patch('services.notifications.send_telegram') as mock_send_telegram:
         
        from core.workflow import process_consensus
        process_consensus(
            context=context,
            signal=None,
            swing_signal=None,
            opinion_shadow=None,
            state={},
            update_state=lambda **kwargs: None,
            summary_callback=lambda text, **kwargs: mock_send_telegram(text)
        )
        
        # 1. No signal was saved
        mock_save_signal.assert_not_called()
        
        # 2. Telegram alert WAS sent because trigger_type == 'manual'
        mock_send_telegram.assert_called_once()
        summary_text = mock_send_telegram.call_args[0][0]
        assert "ПРОПУЩЕН" in summary_text
