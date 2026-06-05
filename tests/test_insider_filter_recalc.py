# tests/test_insider_filter_recalc.py
import pytest
from core.insider_filter import evaluate_wallet, recalculate_all_insiders
from core.stats import binomial_pvalue
from agents.shared.python.db import get_wallets_for_pvalue_recalc


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """Подменяет DB_PATH на временный файл."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("agents.shared.python.db.DB_PATH", db_file)
    # Сбросить флаг инициализации
    import agents.shared.python.db as db_module
    monkeypatch.setattr(db_module, "_db_initialized", False)
    yield db_file


def test_recalculate_includes_manual_n_trades():
    """Кошелёк с n_trades>0 но tx_count=0 должен попасть в пересчёт."""
    mock_wallets = [
        {"address": "0xAAA", "n_trades": 20, "n_wins": 15, "tx_count": 0, "computed_wins": None}
    ]
    captured = []
    
    verdicts = recalculate_all_insiders(
        fetch_wallets=lambda: mock_wallets,
        save_verdict=lambda **kw: captured.append(kw),
    )
    
    assert any(v.address == "0xAAA" for v in verdicts)
    assert len(captured) == 1
    assert captured[0]["address"] == "0xAAA"
    assert captured[0]["n_trades"] == 20
    assert captured[0]["n_wins"] == 15


def test_evaluate_wallet_low_n_trades_returns_not_insider():
    """При n_trades < MIN_TRADES → is_insider=False."""
    v = evaluate_wallet("0xBBB", n_trades=5, n_wins=5)
    assert v.is_insider is False
    assert v.p_value == 1.0


def test_binomial_pvalue_large_n_no_overflow():
    """При n=1000, k=600 не должно быть OverflowError или отрицательного p-value."""
    pv = binomial_pvalue(1000, 600, p0=0.5)
    assert 0.0 <= pv <= 1.0


def test_get_wallets_for_pvalue_recalc_includes_manual_wallets(isolated_db):
    """Кошельки с n_trades > 0 должны включаться даже без транзакций в БД."""
    from agents.shared.python.db import get_connection, init_db, upsert_known_whale
    
    # Инициализируем БД и вставляем кошелек с ручными trades
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM wallets")
        conn.execute("DELETE FROM trader_transactions")
        
    # upsert_known_whale(address, alias, win_rate, total_profit)
    upsert_known_whale("0xmanual", "ManualWhale", 0.75, total_profit=1000.0)
    # Ручной апдейт n_trades
    with get_connection() as conn:
        conn.execute("UPDATE wallets SET n_trades = 20, n_wins = 15 WHERE address = '0xmanual'")
        
    wallets = get_wallets_for_pvalue_recalc()
    addresses = [w["address"] for w in wallets]
    assert "0xmanual" in addresses

