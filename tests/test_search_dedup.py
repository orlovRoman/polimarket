import pytest
from agents.shared.utils.web_search import deduplicate_results

def test_deduplicate_results_basic():
    # Проверка удаления полных дубликатов строк
    rss = ["[2026-05-31 12:00] Breaking news", "Another story"]
    grounding = ["Breaking news", "[HN, ↑10] Another story"]
    
    # "Breaking news" и "[2026-05-31 12:00] Breaking news" должны распознаться как один элемент (после очистки даты)
    # "Another story" и "[HN, ↑10] Another story" также должны объединиться
    res = deduplicate_results(rss, grounding)
    
    assert len(res) == 2
    # Проверяем, что сохранились первые встреченные форматы
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
