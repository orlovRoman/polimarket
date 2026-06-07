import pytest
import sqlite3
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import agents.shared.python.db as db_module
from agents.shared.python.db import (
    get_signal_by_id, get_signal_analysis_report, save_memory, save_signal
)
from core.models import Signal

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная БД для каждого теста."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db_module, "_db_initialized", False)
    db_module.init_db()
    yield

def test_get_signal_by_id_full_match():
    """get_signal_by_id возвращает сигнал по полному UUID."""
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO markets (id, platform, title, url, outcome, price, close_time) "
            "VALUES ('mkt-001', 'polymarket', 'Test', 'http://x', 'YES', 0.5, '2099-01-01')"
        )
        conn.execute(
            "INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details) "
            "VALUES ('sig-uuid-001', 'scout', 'mkt-001', 'polymarket', 0.7, 'high', 'summary', '{}')"
        )
    sig = get_signal_by_id("sig-uuid-001")
    assert sig is not None
    assert sig["id"] == "sig-uuid-001"

def test_get_signal_by_id_truncated():
    """get_signal_by_id находит сигнал по усечённому ID (LIKE)."""
    from agents.shared.python.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO markets (id, platform, title, url, outcome, price, close_time) "
            "VALUES ('mkt-002', 'polymarket', 'Test2', 'http://y', 'YES', 0.4, '2099-01-01')"
        )
        conn.execute(
            "INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details) "
            "VALUES ('sig-uuid-long-001', 'scout', 'mkt-002', 'polymarket', 0.6, 'medium', 'sum', '{}')"
        )
    # Передаём первые 10 символов
    sig = get_signal_by_id("sig-uuid-l")
    assert sig is not None
    assert sig["id"] == "sig-uuid-long-001"

def test_get_signal_by_id_not_found():
    """get_signal_by_id возвращает None для несуществующего ID."""
    result = get_signal_by_id("nonexistent-id-xxxxx")
    assert result is None

def test_get_signal_analysis_report_found():
    """get_signal_analysis_report находит отчёт по prefix-match."""
    save_memory("consensus_report_sig-test-001", "<b>Отчёт</b>", category="fact")
    report = get_signal_analysis_report("sig-test-0")  # усечённый
    assert report == "<b>Отчёт</b>"

def test_get_signal_analysis_report_not_found():
    """get_signal_analysis_report возвращает None если отчёта нет."""
    result = get_signal_analysis_report("no-report-here")
    assert result is None

def test_del_sig_callback_data_parse():
    """
    Тест парсинга callback_data формата del_sig_{page}_{truncated_id}
    включая edge-case когда truncated_id содержит _.
    """
    callback_data = "del_sig_2_fc_market_abc"
    _, _, page_str, truncated_id = callback_data.split("_", 3)
    assert page_str == "2"
    assert truncated_id == "fc_market_abc"
    assert int(page_str) == 2

def test_del_sig_callback_data_simple():
    """Стандартный UUID-based truncated_id не содержит _, парсится корректно."""
    callback_data = "del_sig_0_550e8400e29b41d4"
    _, _, page_str, truncated_id = callback_data.split("_", 3)
    assert page_str == "0"
    assert truncated_id == "550e8400e29b41d4"

def test_html_cleanup_removes_all_tags():
    """re.sub удаляет теги включая <a href=...>."""
    html = "<b>Текст</b> <a href='http://x'>ссылка</a> и <i>курсив</i>"
    clean = re.sub(r'<[^>]+>', '', html)
    assert "<" not in clean
    assert "href" not in clean
    assert "Текст" in clean
    assert "ссылка" in clean
