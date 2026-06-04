import logging
import os
import tempfile
import pytest
from unittest.mock import patch

def test_agent_reports_logger_has_handler_before_setup():
    """AgentReports логгер должен быть готов к записи даже если
    setup_logger ещё не вызывался — через get_report_logger()."""
    from config import get_report_logger
    log = get_report_logger()
    assert len(log.handlers) > 0, \
        "AgentReports не имеет handler'ов — вызовы info() уйдут в никуда"

def test_agent_reports_logger_idempotent():
    """Повторный вызов get_report_logger() не должен дублировать handler'ы."""
    from config import get_report_logger
    get_report_logger()
    log = get_report_logger()
    handler_count = len(log.handlers)
    get_report_logger()
    assert len(log.handlers) == handler_count, \
        "Каждый вызов get_report_logger() добавляет лишний handler"

def test_setup_logger_idempotent():
    """Двойной вызов setup_logger() не должен дублировать handler'ы."""
    from config import setup_logger
    log1 = setup_logger("TestLogger_idempotent")
    count_after_first = len(log1.handlers)
    log2 = setup_logger("TestLogger_idempotent")
    assert len(log2.handlers) == count_after_first, \
        f"setup_logger дублирует handler'ы: {count_after_first} → {len(log2.handlers)}"

def test_report_logger_actually_writes(tmp_path):
    """Запись через report_logger реально попадает в файл."""
    from config import LOGS_DIR, AGENT_REPORTS_PATH
    log = logging.getLogger("AgentReports")
    # Добавляем временный FileHandler для теста
    tmp_log = tmp_path / "test_reports.log"
    fh = logging.FileHandler(str(tmp_log), encoding="utf-8")
    fh.setLevel(logging.INFO)
    log.addHandler(fh)
    
    log.info("TEST_ENTRY_12345")
    fh.flush()
    log.removeHandler(fh)
    
    content = tmp_log.read_text(encoding="utf-8")
    assert "TEST_ENTRY_12345" in content, "Запись не попала в файл"

def test_no_duplicate_import_json_in_agent():
    """В methods класса NexusAgent не должно быть inline `import json`."""
    import inspect
    from agents.orchestrator.src.agent import NexusAgent
    src = inspect.getsource(NexusAgent.screen_markets)
    assert "import json" not in src, \
        "import json внутри метода screen_markets() — нужно убрать, он уже есть на уровне модуля"

def test_report_message_truncated():
    """Агентский отчёт не должен логировать неограниченный текст."""
    from agents.orchestrator.src.agent import NexusAgent
    import inspect
    src = inspect.getsource(NexusAgent.process_prompt)
    # Проверяем что есть обрезка ([:NNNNN] или truncated)
    assert "truncated" in src or "[:3000]" in src or "MAX_REPORT" in src, \
        "text_response в report_logger.info() не обрезается — риск переполнения лога"
