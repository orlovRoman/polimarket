# tests/test_prompt_guards.py
from datetime import datetime
from agents.shared.utils.prompt_guards import (
    guard_description, guard_orderbook, guard_smart_money, guard_news_with_age
)

def test_guard_description_empty_returns_warning():
    result = guard_description("")
    assert "ОТСУТСТВУЕТ" in result
    assert "НЕ ПРИДУМЫВАЙ" in result

def test_guard_description_short_returns_warning():
    result = guard_description("Short.")
    assert "ОТСУТСТВУЕТ" in result

def test_guard_description_full_contains_oracle_task():
    desc = "This market resolves YES if CME closes above $80 on May 30."
    result = guard_description(desc)
    assert "CME" in result
    assert "ЗАДАЧА ПО ОРАКУЛУ" in result
    assert "ПРАВИЛА РАЗРЕШЕНИЯ" in result

def test_guard_orderbook_none_returns_ban():
    result = guard_orderbook(None)
    assert "НЕДОСТУПЕН" in result
    assert "НЕ ПРИДУМЫВАЙ" in result
    assert "confidence=0.30" in result

def test_guard_orderbook_data_includes_ratio():
    ob = {"spread": "2.1%", "top_bid": 0.61, "top_ask": 0.63,
          "bid_depth_5": 1200, "ask_depth_5": 400, "total_bids": 8, "total_asks": 5}
    result = guard_orderbook(ob)
    assert "3.0x" in result  # 1200/400
    assert "бычий сигнал" in result

def test_guard_smart_money_no_data_bans_mention():
    result = guard_smart_money(None, "YES")
    assert "НЕ УПОМИНАЙ Smart Money" in result

def test_guard_news_empty_returns_catalyst_ban():
    result = guard_news_with_age([])
    assert "catalyst_absence_reason" in result

def test_guard_news_old_item_tagged():
    from datetime import timedelta
    old_news = [{"title": "Old event happened", "published_parsed": None,
                 "published": (datetime.utcnow() - timedelta(hours=80)).isoformat()}]
    result = guard_news_with_age(old_news)
    assert "НЕ КАТАЛИЗАТОР" in result

def test_guard_news_fresh_item_tagged_hot():
    from datetime import timedelta
    fresh = [{"title": "Breaking news now", "published_parsed": None,
              "published": (datetime.utcnow() - timedelta(hours=2)).isoformat()}]
    result = guard_news_with_age(fresh)
    assert "СВЕЖИЙ КАТАЛИЗАТОР" in result
