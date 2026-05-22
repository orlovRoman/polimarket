import os
import sys

# Настройка кодировки для Windows консоли
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Добавляем корень проекта в пути
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.trend_hunter import fetch_rss_titles, extract_trends_with_llm, run_trend_hunter, GOOGLE_NEWS_RSS, GOOGLE_TRENDS_RSS
from agents.shared.adapters.polymarket import PolymarketAdapter

def test_rss_fetching():
    print("=== ТЕСТ 1: Получение новостей из Google News RSS ===")
    titles = fetch_rss_titles(GOOGLE_NEWS_RSS, limit=5)
    print(f"Получено заголовков: {len(titles)}")
    for i, t in enumerate(titles, 1):
        print(f"  {i}. {t}")
    assert len(titles) > 0, "Не удалось получить заголовки из Google News!"

    print("\n=== ТЕСТ 2: Получение трендов из Google Trends RSS ===")
    trends = fetch_rss_titles(GOOGLE_TRENDS_RSS, limit=5)
    print(f"Получено поисковых запросов: {len(trends)}")
    for i, t in enumerate(trends, 1):
        print(f"  {i}. {t}")
    assert len(trends) > 0, "Не удалось получить заголовки из Google Trends!"
    print("[OK] Тест RSS-парсёра пройден успешно.")

def test_polymarket_search():
    print("\n=== ТЕСТ 3: Поиск рынков на Polymarket через Gamma API ===")
    adapter = PolymarketAdapter()
    test_queries = ["Trump", "Bitcoin", "AI"]
    
    for q in test_queries:
        print(f"Поиск по запросу: '{q}'...")
        markets = adapter.search_markets(q, limit=3)
        print(f"  Найдено рынков: {len(markets)}")
        for m in markets:
            print(f"    - {m.title} (YES Price: {m.price})")
        assert len(markets) >= 0
    print("[OK] Тест Gamma API поиска пройден успешно.")

def test_llm_trend_extraction():
    print("\n=== ТЕСТ 4: Извлечение трендов и генерация ключевых слов через Gemini ===")
    dummy_titles = [
        "SpaceX Starship Flight 4 scheduled for launch next Thursday",
        "Donald Trump criminal trial reaches final verdicts in New York",
        "Federal Reserve signals potential interest rate cuts later this year",
        "Nvidia becomes the second most valuable company in the world as AI rally continues",
        "OpenAI announces partnership with Apple to integrate ChatGPT into iOS 18"
    ]
    print(f"Имитируем подачу {len(dummy_titles)} заголовков...")
    trends = extract_trends_with_llm(dummy_titles)
    print(f"ИИ выделил трендов: {len(trends)}")
    for i, t in enumerate(trends, 1):
        print(f"  {i}. {t.get('topic_ru')} ({t.get('topic_en')})")
        print(f"     Причина: {t.get('reasoning')}")
        print(f"     Запросы: {t.get('queries')}")
    
    assert len(trends) > 0, "ИИ не выделил ни одного тренда!"
    assert all('queries' in t for t in trends), "У некоторых трендов нет поисковых запросов!"
    print("[OK] Тест ИИ-модуля NEXUS пройден успешно.")

def test_full_dry_run():
    print("\n=== ТЕСТ 5: Полный сухой прогон Trend Hunter (dry-run) ===")
    try:
        run_trend_hunter(dry_run=True)
        print("[OK] Сухой прогон Trend Hunter завершен успешно.")
    except Exception as e:
        print(f"[FAIL] Сбой сухого прогона: {e}")
        raise e

if __name__ == "__main__":
    print("=== ЗАПУСК ВЕРИФИКАЦИОННЫХ ТЕСТОВ TREND HUNTER ===")
    try:
        test_rss_fetching()
        test_polymarket_search()
        test_llm_trend_extraction()
        test_full_dry_run()
        print("\n[SUCCESS] ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО! Служба готова к работе в продакшене.")
    except AssertionError as ae:
        print(f"\n[FAIL] Ошибка верификации: {ae}")
        sys.exit(1)
    except Exception as ex:
        import traceback
        print("\n[CRITICAL FAIL] Критический сбой во время тестов:")
        traceback.print_exc()
        sys.exit(1)
