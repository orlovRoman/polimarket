import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from agents.shared.utils.web_search import deduplicate_results
from core.workflow import run_agent_evaluation
from core.models import Market

def test_deduplicate_results_basic():
    # Проверка удаления полных дубликатов строк
    rss = ["[2026-05-31 12:00] Breaking news", "Another story"]
    grounding = ["Breaking news", "[HN, ↑10] Another story"]
    
    res = deduplicate_results(rss, grounding)
    
    assert len(res) == 2
    assert res[0] == "[2026-05-31 12:00] Breaking news"
    assert res[1] == "Another story"

def test_deduplicate_results_dicts():
    # Проверка работы со словарями
    rss = [{"title": "Market pump"}, {"title": "[2026-06-01] Market crash"}]
    grounding = [{"title": "Market pump"}, {"title": "Market crash"}]
    
    res = deduplicate_results(rss, grounding)
    assert len(res) == 2
    assert res[0]["title"] == "Market pump"
    assert res[1]["title"] == "[2026-06-01] Market crash"

def test_deduplicate_results_empty():
    assert deduplicate_results([], []) == []
    assert deduplicate_results(None, None) == []

def test_deduplicate_case_insensitive():
    # Проверка регистронезависимости (BUG-DD-03)
    rss = ["[2026-06-01] Breaking News"]
    grounding = ["breaking news"]
    res = deduplicate_results(rss, grounding)
    assert len(res) == 1, f"BUG-DD-03: регистр не нормализован, найдено {len(res)} вместо 1"

@pytest.mark.asyncio
async def test_dedup_called_in_context_pipeline():
    # Проверка вызова дедупликации в рабочем пайплайне (BUG-DD-02)
    m = Market(
        id="test_m_pipeline",
        platform="polymarket",
        title="Will Bitcoin reach 100k?",
        description="Bitcoin reaching 100k",
        url="http://test",
        outcome="YES",
        price=0.5,
        close_time=datetime.now()
    )
    scout = MagicMock()
    scout.api_key = "test_key"
    scout.model = "gemini-2.5-flash"
    swing = MagicMock()
    swing.api_key = "test_key"
    swing.model = "gemini-2.5-flash"
    update_state = MagicMock()

    with patch("agents.shared.utils.web_search.deduplicate_results",
               wraps=deduplicate_results) as mock_dedup, \
         patch("core.workflow.fetch_rss_news", return_value=["News"]), \
         patch("core.workflow.fetch_reddit_news", return_value=["Reddit"]), \
         patch("core.workflow._fetch_grounded_context", return_value="Grounded News"), \
         patch("core.workflow.fetch_google_trends", return_value=""), \
         patch("core.workflow.get_memory", return_value=None):
        
        try:
            await run_agent_evaluation(m, scout, swing, update_state)
        except Exception:
            # Игнорируем возможные дальнейшие ошибки (например, вызовы агентов),
            # так как нам важен только этап сбора и дедупликации контекста
            pass
            
        assert mock_dedup.called, "BUG-DD-02: deduplicate_results не вызывается при построении контекста"
