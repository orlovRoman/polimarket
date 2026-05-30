import pytest
from agents.shared.python.db import init_db, get_connection, get_known_whales, save_trader_transaction

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM wallets")
        conn.execute("DELETE FROM trader_transactions")

def test_known_whales_total_vol_differs_from_total_won():
    # Insert a transaction which will automatically register the wallet
    # Wallet address is lowercased inside smart money and known whales lookup
    addr = "0xwhalespecial"
    save_trader_transaction(
        wallet_address=addr,
        market_id="mkt_test",
        outcome="YES",
        amount_usd=15000.0,
        price=0.5,
        alias="WhaleSpecial"
    )
    
    # Update profit and win rate statically in wallets table
    with get_connection() as conn:
        conn.execute(
            "UPDATE wallets SET win_rate = ?, total_profit = ? WHERE address = ?",
            (0.8, 5000.0, addr)
        )
        
    whales = get_known_whales()
    
    assert addr in whales
    whale = whales[addr]
    assert whale["alias"] == "WhaleSpecial"
    assert whale["win_rate"] == 0.8
    assert whale["total_won"] == 5000.0
    assert whale["total_vol"] == 15000.0  # Should match the sum of transactions!
    assert whale["total_vol"] != whale["total_won"], "Ошибка: total_vol и total_won одинаковы!"
