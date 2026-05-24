import os
import json
from datetime import datetime
from typing import Optional
from agents.shared.python.models import Market, Signal
from agents.shared.python.db import save_signal, get_connection, get_memory, get_market_correlations
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news

class ScoutAgent:
    """
    Агент SCOUT — основной аналитический модуль для поиска недооцененных рынков.
    Специализируется на выявлении математического преимущества (Edge) путем 
    сравнения собственной оценки вероятности события с текущей рыночной ценой.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        """
        Инициализация агента SCOUT. Инструкции загружаются из локального GEMINI.md.
        """
        self.api_key = api_key
        self.model = model
        
        # Загружаем детальные системные инструкции из файла конфигурации агента
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def estimate_market(self, market: Market) -> Optional[Signal]:
        """
        Оценивает рынок и формирует торговый сигнал, если найдена недооценка.
        Честный Double-Blind: оценка производится без передачи цены в LLM.
        
        :param market: Данные о рынке Polymarket
        :return: Объект Signal, если Edge > 0.10, иначе None
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Загружаем RAG-память из Obsidian
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SCOUT] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        print(f"  SCOUT ищет базовые данные (RSS + Reddit + Корреляции) для оценки: {market.title}...")
        news_titles = fetch_rss_news(market.title)
        reddit_posts = fetch_reddit_news(market.title)
        
        # Получаем известные корреляции из базы
        correlations = get_market_correlations(market.id)
        correlation_texts = []
        if correlations:
            from agents.shared.adapters.polymarket import PolymarketAdapter
            adapter = PolymarketAdapter()
            for corr in correlations:
                # Определяем ID связанного рынка
                related_id = corr["market_id_b"] if corr["market_id_a"] == market.id else corr["market_id_a"]
                related_title = corr["title_b"] if corr["market_id_a"] == market.id else corr["title_a"]
                
                # Получаем свежую цену связанного рынка
                try:
                    related_market = adapter.get_market(related_id)
                    related_price_text = f"АКТУАЛЬНАЯ ЦЕНА: {related_market.price}" if related_market else "Цена неизвестна"
                except:
                    related_price_text = "Ошибка получения цены"
                    
                correlation_texts.append(
                    f"- Связанный рынок: '{related_title}' ({related_price_text})\n"
                    f"  Тип связи: {corr['correlation_type']}\n"
                    f"  Описание: {corr['description']}"
                )

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Описание: {market.description}
Исход: {market.outcome}

{rag_context}

Последние заголовки RSS (для справки):
{chr(10).join(news_titles) if news_titles else "RSS новостей не найдено."}

Последние посты с Reddit (для справки):
{chr(10).join(reddit_posts) if reddit_posts else "Постов на Reddit не найдено."}

[Известные кросс-рыночные корреляции]
{chr(10).join(correlation_texts) if correlation_texts else "Известных корреляций нет."}

Используй известные корреляции (и цены связанных рынков) как жесткую математическую базу. Если связанный рынок оценен выше или ниже, и между ними есть прямая или обратная связь — используй это для вычисления математического арбитража. 
Используй инструмент google_search, чтобы найти актуальную статистику, если корреляций недостаточно.
Затем выполни анализ согласно своим инструкциям.
Ответ верни строго в формате JSON: {{"estimate_probability": 0.65, "confidence": 0.8, "priority": "high", "reasoning": "..."}}
"""
        
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": [{"google_search": {}}],
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback
        result, active_model = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="SCOUT"
        )
        
        if not result:
            return None
            
        try:
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            import re
            json_match = re.search(r'\{[^{}]*"estimate_probability"[^{}]*\}', content, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                print(f"[SCOUT] Не удалось распарсить JSON из ответа: {content[:100]}")
                return None
            
            # Рассчитываем математическое преимущество (Edge) на уровне Python (честный Double-Blind)
            est_prob = float(analysis.get("estimate_probability", 0))
            edge = est_prob - market.price
            
            # Порог активации: настраиваемый через /settings (дефолт 10%)
            min_edge = get_memory("min_edge")
            if min_edge is None:
                min_edge = 0.10
            if edge > min_edge:
                signal = Signal(
                    id=f"sig-{market.id}-{int(datetime.now().timestamp())}",
                    type="undervaluation",
                    market_id=market.id,
                    platform=market.platform,
                    edge=edge,
                    confidence=analysis.get("confidence", 0.5),
                    priority=analysis.get("priority", "medium"),
                    summary=f"Недооценка на {edge*100:.1f}%: {market.title}",
                    details=analysis.get("reasoning", "")
                )
                return signal
        except Exception as e:
            print(f"Ошибка при оценке рынка {market.id}: {e}")
            
        return None

    def run_scan(self, limit: int = 10):
        """
        Запускает цикл сканирования рынков из базы данных.
        
        :param limit: Количество рынков для проверки
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM markets ORDER BY updated_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
        
        print(f"Запуск сканирования {len(rows)} рынков...")
        
        for row in rows:
            market = Market(
                id=row['id'],
                platform=row['platform'],
                title=row['title'],
                description=row['description'],
                url=row['url'],
                outcome=row['outcome'],
                price=row['price'],
                close_time=datetime.fromisoformat(row['close_time'])
            )
            
            print(f"Анализируем: {market.title} (Цена: {market.price})...")
            signal = self.estimate_market(market)
            if signal:
                print(f"!!! НАЙДЕН СИГНАЛ: {signal.summary} (Edge: {signal.edge:.2f}, Conf: {signal.confidence})")
                save_signal(signal)
            else:
                print("--- Сигнал не найден.")

if __name__ == "__main__":
    # Локальный запуск агента для тестов
    from dotenv import load_dotenv
    load_dotenv()
    
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        print("GOOGLE_API_KEY не найден в .env")
    else:
        scout = ScoutAgent(api_key=key)
        scout.run_scan()
