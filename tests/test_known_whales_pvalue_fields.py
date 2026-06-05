# tests/test_known_whales_pvalue_fields.py
import sqlite3
from unittest.mock import patch, MagicMock
from agents.shared.python.db import get_known_whales


def test_get_known_whales_returns_n_trades_and_n_wins():
    """NEW-BUG-DB-01: get_known_whales должен включать n_trades, n_wins, p_value."""
    mock_row = {
        "address": "0xabc", "alias": "whale1",
        "win_rate": 0.75, "total_profit": 1000.0,
        "is_insider": True,
        "n_trades": 20, "n_wins": 15, "p_value": 0.021,
        "total_vol": 5000.0
    }

    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
        # patch cursor.execute и commit
        mock_conn.return_value.__enter__.return_value.commit = MagicMock()

        whales = get_known_whales()

    assert "0xabc" in whales, "Кошелёк должен присутствовать в словаре"
    w = whales["0xabc"]
    assert "n_trades" in w, "NEW-BUG-DB-01: поле n_trades отсутствует в get_known_whales"
    assert "n_wins" in w, "NEW-BUG-DB-01: поле n_wins отсутствует в get_known_whales"
    assert "p_value" in w, "NEW-BUG-DB-01: поле p_value отсутствует в get_known_whales"
    assert w["n_trades"] == 20
    assert w["n_wins"] == 15
    assert w["p_value"] == 0.021


def test_smart_money_uses_db_n_trades_not_zero(monkeypatch):
    """Интеграционный: analyze_smart_money должен получать n_trades>0 из known_whales."""
    from core.smart_money import analyze_smart_money

    # Имитируем known_whales с n_trades из БД
    fake_whales = {
        "0xabc": {
            "alias": "TestWhale", "win_rate": 0.87,
            "n_trades": 20, "n_wins": 17, "p_value": 0.008,
            "is_insider": True, "total_won": 500.0, "total_vol": 10000.0
        }
    }
    monkeypatch.setattr("core.smart_money.get_known_whales", lambda: fake_whales)

    trades = [{
        "maker_address": "0xabc", "outcome_index": 0,
        "size": 1000.0, "price": 0.7, "time": 9999999999
    }]
    result = analyze_smart_money(trades, [])

    assert result.available
    assert len(result.wallets_list) > 0
    wallet = result.wallets_list[0]
    assert wallet.is_insider, (
        "NEW-BUG-DB-01: is_insider=False, хотя n_trades=20 and n_wins=17 "
        "должны давать p<0.05 — скорее всего n_trades=0 из-за отсутствия поля в get_known_whales"
    )


def test_upsert_known_whale_does_not_force_insider_by_default():
    """NEW-BUG-DB-02: upsert_known_whale не должен ставить is_insider=TRUE без p-value."""
    from unittest.mock import patch, MagicMock

    executed_sql = []
    executed_params = []

    def fake_execute(sql, params=None):
        executed_sql.append(sql)
        executed_params.append(params or [])
        return MagicMock()

    with patch("agents.shared.python.db.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute = fake_execute
        mock_conn.return_value.__enter__.return_value.commit = MagicMock()

        from agents.shared.python.db import upsert_known_whale
        upsert_known_whale("0xBBB", "alias", win_rate=0.55)

    # is_insider не должен быть True безусловно
    params_flat = [str(p) for p in (executed_params[0] if executed_params else [])]
    # Четвёртый параметр (is_insider) должен быть False по умолчанию
    assert "True" not in params_flat or "force_insider" in str(executed_sql), \
        "NEW-BUG-DB-02: upsert_known_whale безусловно пишет is_insider=True"
