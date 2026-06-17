# tests/test_resolution_guard.py
"""Тесты защитных механизмов резолюции рынков (Resolution Guards)."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from services.polymarket_client import get_market_resolution

def test_active_market_not_resolved():
    """Открытый рынок (closed=False) никогда не должен разрешаться."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": False,
        "winner": "YES",
        "tokens": []
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m1")
        assert res is None, "Открытый рынок не должен разрешаться"

def test_negrisk_active_not_resolved():
    """Открытый negRisk-рынок (tokens=[], closed=False, NO=99c) не должен разрешаться."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": False,
        "tokens": [],
        "outcomePrices": "[\"0.01\", \"0.99\"]"
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m2")
        assert res is None, "Открытый negRisk рынок не должен разрешаться"

def test_closed_market_resolved_by_winner():
    """Закрытый рынок должен успешно разрешаться по полю winner."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": True,
        "winner": "NO",
        "tokens": []
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m3")
        assert res == "NO"

def test_closed_market_resolved_by_prices():
    """Закрытый рынок без winner должен разрешаться по outcomePrices."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": True,
        "winner": None,
        "tokens": [],
        "outcomePrices": "[\"0.01\", \"0.99\"]"
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m4")
        assert res == "NO"

def test_uma_resolution_status_not_resolved():
    """Рынок со статусом UMA, отличным от resolved, не должен разрешаться."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": True,
        "umaResolutionStatus": "proposed",
        "winner": "YES",
        "tokens": []
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m5")
        assert res is None, "Рынок со статусом UMA 'proposed' не должен разрешаться"

def test_uma_resolution_status_resolved():
    """Рынок со статусом UMA 'resolved' должен успешно разрешаться."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": True,
        "umaResolutionStatus": "resolved",
        "winner": "YES",
        "tokens": []
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m6")
        assert res == "YES"


def test_negrisk_closed_false_but_ended_resolved():
    """negRisk-рынок с closed=false, но endDate прошёл и цена NO=99.9% → должен разрешаться."""
    from datetime import timedelta
    past_date = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": False,
        "tokens": [],
        "endDate": past_date,
        "outcomePrices": "[\"0.0005\", \"0.9995\"]"
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m_negrisk_ended")
        assert res == "NO", f"negRisk-рынок с endDate в прошлом и NO=99.95% должен разрешиться как NO, получено: {res}"


def test_negrisk_closed_false_not_ended_yet():
    """negRisk-рынок с closed=false и endDate в будущем → НЕ разрешается, даже при NO=99%."""
    from datetime import timedelta
    future_date = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": False,
        "tokens": [],
        "endDate": future_date,
        "outcomePrices": "[\"0.01\", \"0.99\"]"
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m_negrisk_active")
        assert res is None, "negRisk с endDate в будущем не должен разрешаться"


def test_negrisk_closed_false_ended_but_uncertain():
    """negRisk-рынок с endDate в прошлом но ценой NO=95% (< 99.9%) → не разрешается."""
    from datetime import timedelta
    past_date = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "closed": False,
        "tokens": [],
        "endDate": past_date,
        "outcomePrices": "[\"0.05\", \"0.95\"]"  # 95% < порог 99.9%
    }
    with patch("requests.get", return_value=mock_resp):
        res = get_market_resolution("m_negrisk_uncertain")
        assert res is None, "negRisk с неопределённой ценой (95%) не должен разрешаться"


class TestCompoundResolutionGuard:
    @patch("services.outcome_tracker._fetch_resolution", return_value="YES")
    @patch("services.outcome_tracker._resolve_signal")
    @patch("agents.shared.python.db.resolve_compound_opportunity")
    @patch("agents.shared.python.db.get_compound_settings", return_value={"virtual_stake": 50.0})
    @patch("agents.shared.python.db.get_connection")
    def test_compound_not_resolved_before_close(
        self, mock_conn, mock_cfg, mock_resolve_opp, mock_resolve_sig, mock_fetch
    ):
        """Рынок с close_time в будущем не должен попадать в SQL-запрос для резолюции."""
        captured_queries = []

        class FakeCursor:
            def __init__(self, data):
                self.data = data
            def fetchall(self):
                return self.data
            def fetchone(self):
                return self.data[0] if self.data else None

        def smart_execute(query, params=()):
            captured_queries.append(query)
            return FakeCursor([])

        conn_mock = mock_conn.return_value.__enter__.return_value
        conn_mock.execute.side_effect = smart_execute

        from services.outcome_tracker import _resolve_compound_outcomes
        _resolve_compound_outcomes()

        assert len(captured_queries) > 0, "SQL запрос к БД не был выполнен"
        select_query = captured_queries[0]
        
        # Проверяем фильтрацию по времени и усечение некорректных fallback-условий
        assert "'-15 minutes'" in select_query, f"В SQL-запросе нет буфера -15 минут: {select_query}"
        assert "OR m.outcome IN ('YES', 'NO')" not in select_query, f"В SQL-запросе остался некорректный OR: {select_query}"
