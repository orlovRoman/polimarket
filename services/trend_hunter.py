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
from datetime import datetime
from typing import List, Dict, Any

# Добавляем корень проекта в путь поиска модулей
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, get_connection, save_memory, get_memory, save_token_usage
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

GOOGLE_NEWS_RSS = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=US"

def send_telegram_notification(text: str):
    """Отправляет оповещение в Telegram, если они включены в настройках."""
    # Проверяем, включены ли оповещения в БД
    alerts_enabled = get_memory("trend_hunter_alerts_enabled", True)
    if not alerts_enabled:
        print("[Trend Hunter] Оповещения в Telegram отключены пользователем.")
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Trend Hunter] Переменные Telegram не настроены.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Trend Hunter] Ошибка отправки уведомления в Telegram: {e}")

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

    # Получаем выбранную модель из настроек (по умолчанию gemini-2.5-flash)
    selected_model = get_memory("selected_model", "gemini-2.5-flash")
    
    # Резервные модели на случай исчерпания лимитов (429/503)
    models_to_try = [selected_model]
    for m in ["gemini-2.0-flash", "gemini-flash-latest"]:
        if m != selected_model:
            models_to_try.append(m)

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
   Избегайте слишком длинных фраз или общих слов (например, вместо "what will happen to biden next", пишите "biden", "election").

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
            "response_mime_type": "application/json"
        }
    }

    for model in models_to_try:
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GOOGLE_API_KEY}"
        print(f"[Trend Hunter] Попытка обращения к Gemini через модель: {model}...")
        try:
            resp = requests.post(api_url, json=payload, timeout=60)
            resp.raise_for_status()
            res_json = resp.json()
            
            # Логируем расход токенов
            usage_meta = res_json.get('usageMetadata', {})
            input_tokens = usage_meta.get('promptTokenCount', 0)
            output_tokens = usage_meta.get('candidatesTokenCount', 0)
            if input_tokens > 0 or output_tokens > 0:
                try:
                    save_token_usage("NEXUS", model, input_tokens, output_tokens)
                except Exception as e:
                    print(f"[Trend Hunter] Ошибка при сохранении расхода токенов: {e}")

            text = res_json['candidates'][0]['content']['parts'][0]['text']
            data = json.loads(text)
            print(f"[Trend Hunter] Успешно получены тренды от модели {model}.")
            return data.get("trends", [])
        except Exception as e:
            print(f"[Trend Hunter] Предупреждение: модель {model} вернула ошибку: {e}")
            continue

    print("[Trend Hunter] Ошибка: все доступные модели Gemini вернули ошибку!")
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
                    cmd = [sys.executable, "run_team.py", "--market_id", m_id]
                    try:
                        subprocess.Popen(cmd)
                        new_markets_triggered.append(m)
                        print(f"      [RUN] Запущен фоновый анализ для рынка {m_id}!")
                    except Exception as ex:
                        print(f"      [ERROR] Ошибка запуска run_team.py для {m_id}: {ex}")

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
        alert_text += "⏳ <i>Команда агентов (SCOUT → SHADOW → HERALD) уже проводит точечный анализ. Скоро будет отчет!</i>"
        send_telegram_notification(alert_text)
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
