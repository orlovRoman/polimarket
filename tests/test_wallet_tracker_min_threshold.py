import pytest
from services.wallet_tracker import ingest_trades

def test_ingest_trades_skips_small_trades(monkeypatch):
    """Сделки ниже _MIN_TRADE_USD не сохраняются."""
    saved_trades = []
    
    # Мокаем save_trader_transaction
    monkeypatch.setattr(
        "services.wallet_tracker.save_trader_transaction",
        lambda addr, market_id, outcome, usd, price: saved_trades.append((addr, market_id, outcome, usd, price))
    )
    
    trades = [
        {"maker_address": "0xABC", "size": "10", "price": "0.5", "outcome_index": 0},  # $5 — меньше $500
        {"maker_address": "0xXYZ", "size": "2000", "price": "0.5", "outcome_index": 0}  # $1000 — больше $500
    ]
    
    saved = ingest_trades("market_test", trades)
    assert saved == 1
    assert len(saved_trades) == 1
    assert saved_trades[0][0] == "0xXYZ"
