import os
import sys

# Настройка кодировки для Windows консоли
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import json
import urllib.request
import xml.etree.ElementTree as ET
import subprocess
import requests
import threading
from datetime import datetime
from typing import List, Dict, Any
from core.engine import CoreEngine

# Добавляем корень проекта в путь поиска модулей
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, get_connection, save_memory, get_memory
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from services.notifications import send_telegram

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

GOOGLE_NEWS_RSS = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=US"


def fetch_rss_titles(url: str, limit: int = 15) -> List[str]:
    """Скачивает и парсит заголовки из RSS-ленты."""
    titles = []
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        for item in items[:limit]:
            title_node = item.find("title")
            if title_node is not None and title_node.text:
                titles.append(title_node.text.strip())
    except Exception as e:
        print(f"[Trend Hunter] Ошибка при парсинге RSS ({url}): {e}")
    return titles

def extract_trends_with_llm(titles: List[str]) -> List[Dict[str, Any]]:
    """Отправляет заголовки в Gemini для выделения трендов и генерации поисковых запросов."""
    if not GOOGLE_API_KEY:
        print("[Trend Hunter] Критическая ошибка: GOOGLE_API_KEY не задан!")
        return []

    from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
    
    titles_text = "\n".join(f"- {title}" for title in titles)

    prompt = f"""
Вы — проактивный аналитик трендов Trend Hunter для платформы прогнозов Polymarket.
Ниже предоставлен список свежих мировых новостей и популярных поисковых запросов Google:

=== НОВОСТИ И ТРЕНДЫ ===
{titles_text}

ЗАДАЧА:
1. Выделите 3-5 наиболее актуальных, резонансных и конкретных событий или трендов из этого списка, по которым с высокой вероятностью могут быть открыты рынки прогнозов (политика, наука, технологии, бизнес, крупные мировые события).
2. Для каждого из выделенных трендов составьте 2-3 коротких, точных поисковых запроса на английском языке (search queries) для поиска соответствующих рынков на платформе Polymarket. 
   Примеры хороших запросов: "Trump trial", "Starship flight", "Apple Vision Pro", "OpenAI GPT-5", "fed interest rate", "inflation".
   Избегайте слишком длинных фраз или общих слов.

Ответьте строго в формате JSON по следующей схеме:
{{
  "trends": [
    {{
      "topic_ru": "Название тренда на русском (например: Испытание Starship)",
      "topic_en": "Название тренда на английском (например: Starship Flight 4)",
      "reasoning": "Краткое обоснование на русском, почему это важно для рынка прогнозов",
      "queries": ["starship", "spacex", "elon musk"]
    }}
  ]
}}
"""

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    res_json, model_used = generate_content_with_fallback(
        api_key=GOOGLE_API_KEY,
        payload=payload,
        default_model="gemini-2.5-flash",
        agent_name="TREND_HUNTER"
    )

    if not res_json:
        print("[Trend Hunter] Ошибка: LLM не вернул ответ.")
        return []

    try:
        text = extract_response_text(res_json)
        data = json.loads(text)
        print(f"[Trend Hunter] Успешно получены тренды от модели {model_used}.")
        return data.get("trends", [])
    except Exception as e:
        print(f"[Trend Hunter] Ошибка при разборе ответа: {e}")
        return []

def is_market_analyzed(market_id: str) -> bool:
    """Проверяет, анализировался ли рынок ранее (есть ли он в analyzed_markets)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM analyzed_markets WHERE market_id = ?", (market_id,))
        return cursor.fetchone() is not None

def run_trend_hunter(dry_run: bool = False):
    """Основной рабочий цикл Trend Hunter."""
    start_time = datetime.now()
    print(f"\n[{start_time}] [START] Запуск службы Trend Hunter (dry_run={dry_run})...")

    # 1. Загружаем заголовки
    print("[Trend Hunter] Загрузка новостей Google News RSS...")
    news_titles = fetch_rss_titles(GOOGLE_NEWS_RSS, limit=15)
    print(f"[Trend Hunter] Получено новостей: {len(news_titles)}")

    print("[Trend Hunter] Загрузка трендов Google Trends RSS...")
    trends_titles = fetch_rss_titles(GOOGLE_TRENDS_RSS, limit=15)
    print(f"[Trend Hunter] Получено поисковых запросов: {len(trends_titles)}")

    all_titles = list(set(news_titles + trends_titles))
    if not all_titles:
        print("[Trend Hunter] Нет данных для анализа. Завершение.")
        return

    # 2. Выделяем тренды через LLM
    print(f"[Trend Hunter] Всего уникальных заголовков для анализа: {len(all_titles)}")
    print("[Trend Hunter] Отправка данных в Gemini...")
    trends = extract_trends_with_llm(all_titles)
    print(f"[Trend Hunter] Выделено трендов: {len(trends)}")

    if dry_run:
        print("\n=== РЕЗУЛЬТАТЫ ИИ-ВЫДЕЛЕНИЯ ТРЕНДОВ (DRY RUN) ===")
        print(json.dumps(trends, indent=2, ensure_ascii=False))

    # 3. Ищем рынки на Polymarket по сгенерированным запросам
    adapter = PolymarketAdapter()
    found_markets_count = 0
    new_markets_triggered = []

    for t in trends:
        topic_ru = t.get("topic_ru", "Без названия")
        queries = t.get("queries", [])
        
        print(f"\n[Тренд]: {topic_ru}")
        print(f"  Поисковые запросы: {queries}")

        unique_markets_for_trend = {}
        for q in queries:
            try:
                markets = adapter.search_markets(q, limit=5)
                for m in markets:
                    unique_markets_for_trend[m.id] = m
            except Exception as e:
                print(f"  Ошибка поиска по запросу '{q}': {e}")

        print(f"  Найдено активных рынков на Polymarket: {len(unique_markets_for_trend)}")

        for m_id, m in unique_markets_for_trend.items():
            analyzed = is_market_analyzed(m_id)
            status_str = "проанализирован ранее" if analyzed else "НОВЫЙ РЫНОК!"
            print(f"    - [{status_str}] {m.title} (ID: {m_id}) | Цена: {m.price}")

            if not analyzed:
                found_markets_count += 1
                if not dry_run:
                    # Сохраняем рынок в БД
                    save_market(m)
                    # Запускаем фоновый точечный командный анализ
                    # Баг #4: захватываем m_id и topic_ru по значению через default args,
                    # иначе closure захватывает переменные по ссылке — все потоки
                    # запускаются с последним m_id цикла.
                    def _run_trend_hunter_scan(_mid=m_id, _topic=topic_ru):
                        eng = CoreEngine()
                        eng.run_team_discussion(
                            log_callback=None,
                            summary_callback=None,
                            category=None,
                            market_id=_mid,
                            state_callback=None,
                            trigger_type="event_driven",
                            source_url="https://trends.google.com/",
                            source_text=f"🎯 Trend Hunter: {_topic}",
                            triggered_at=datetime.now()
                        )
                    # Баг #5: try/except вокруг thread.start(), а не после него
                    try:
                        threading.Thread(target=_run_trend_hunter_scan, daemon=True).start()
                        new_markets_triggered.append(m)
                        print(f"      [RUN] Запущен фоновый анализ для рынка {m_id}!")
                    except Exception as ex:
                        print(f"      [ERROR] Ошибка запуска для {m_id}: {ex}")

    # 4. Отправляем сводное оповещение в Telegram (если были новые рынки)
    if new_markets_triggered and not dry_run:
        alert_text = (
            f"🎯 <b>Проактивный Trend Hunter обнаружил новые рынки!</b>\n\n"
            f"Всего новых рынков отправлено на ИИ-консенсус: <b>{len(new_markets_triggered)}</b>\n\n"
        )
        for i, m in enumerate(new_markets_triggered, 1):
            alert_text += (
                f"<b>{i}. {m.title}</b>\n"
                f"💰 Текущая цена YES: <code>{int(round(m.price * 100))}¢</code>\n"
                f"🔗 <a href='{m.url}'>Открыть на Polymarket</a>\n\n"
            )
        alert_text += "⏳ <i>Команда агентов (SCOUT → SWING → SHADOW) уже проводит точечный анализ. Скоро будет отчет!</i>"
        alerts_enabled = get_memory("trend_hunter_alerts_enabled", True)
        if alerts_enabled:
            send_telegram(alert_text)
        else:
            print("[Trend Hunter] Оповещения в Telegram отключены пользователем.")
    elif dry_run:
        print(f"\n[DRY RUN] Всего обнаружено новых рынков: {found_markets_count}")

    # Записываем время последнего запуска в БД (даже в dry_run для отладки)
    if not dry_run:
        save_memory("trend_hunter_last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    print(f"[{end_time}] [FINISHED] Работа Trend Hunter завершена за {duration:.1f} сек.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Проактивный Trend Hunter для Polymarket")
    parser.add_argument("--dry-run", action="store_true", help="Запустить в режиме тестирования без записи в БД и вызова ИИ")
    args = parser.parse_args()
    
    run_trend_hunter(dry_run=args.dry_run)
