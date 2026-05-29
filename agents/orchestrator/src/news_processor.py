import os
import re
import json
from typing import Optional, List
import logging
from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
from agents.shared.adapters.polymarket import PolymarketAdapter
from core.models import Market

logger = logging.getLogger("NewsProcessor")

class NewsProcessor:
    """
    Модуль для обработки сырых текстовых новостей (например, из Telegram-каналов)
    с помощью NEXUS (gemini) и поиска релевантных рынков на Polymarket.

    Двухэтапный процесс:
    1. LLM извлекает ключевые слова из новости → поиск рынков через Polymarket API
    2. LLM валидирует: действительно ли найденные рынки связаны с новостью
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.adapter = PolymarketAdapter()

    def _validate_relevance(self, text: str, markets: List[Market]) -> List[Market]:
        """
        Второй этап: LLM проверяет, действительно ли найденные рынки
        связаны с текстом новости. Отсеивает ложные срабатывания search API.

        Polymarket API search иногда возвращает нерелевантные рынки
        (например, 'Jesus Christ vs GTA VI' по запросу 'uranium').
        Этот метод отсеивает такой мусор.
        """
        if not markets:
            return []

        # Формируем нумерованный список рынков для LLM
        market_list = "\n".join(
            f"{i+1}. {m.title}"
            for i, m in enumerate(markets)
        )

        prompt = f"""
Ты — финансовый аналитик. Определи, какие из предложенных рынков предсказаний (prediction markets)
РЕАЛЬНО связаны с данной новостью.

Текст новости:
"{text[:1500]}"

Найденные рынки:
{market_list}

Верни JSON с массивом "relevant_indices" — номера (начиная с 1) рынков, которые РЕАЛЬНО связаны с новостью.
Если НИ ОДИН рынок не связан с новостью — верни пустой массив.

ВАЖНО:
- Рынок связан с новостью, только если новость НАПРЯМУЮ может повлиять на исход этого рынка.
- Совпадение по одному слову — НЕ достаточно. Нужна тематическая/причинно-следственная связь.
- Будь СТРОГИМ: лучше вернуть пустой массив, чем включить нерелевантный рынок.

Пример:
Новость: "США вводят санкции против TSMC"
Рынки:
  1. Will TSMC stock drop below $100?
  2. Will Jesus return before GTA VI?
Ответ: {{"relevant_indices": [1]}}
"""

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "systemInstruction": {
                "parts": [{"text": "Ты — финансовый аналитик. Отвечай строго в JSON. Будь строгим при оценке релевантности."}]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
            }
        }

        result, _ = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="NEXUS_NEWS_VALIDATE"
        )

        if not result:
            logger.warning("[NewsProcessor] Ошибка LLM при валидации релевантности. Отклоняем все рынки (safe fallback).")
            return []

        try:
            content = extract_response_text(result)
            data = json.loads(content)
            relevant_indices = data.get("relevant_indices", [])

            if not relevant_indices:
                logger.info("[NewsProcessor] LLM: ни один найденный рынок не связан с новостью.")
                return []

            # Фильтруем: оставляем только рынки с валидными индексами
            validated = []
            for idx in relevant_indices:
                if isinstance(idx, int) and 1 <= idx <= len(markets):
                    validated.append(markets[idx - 1])

            logger.info(
                f"[NewsProcessor] Валидация релевантности: "
                f"{len(validated)}/{len(markets)} рынков прошли проверку"
            )
            for m in validated:
                logger.info(f"  ✅ Релевантен: {m.title}")

            return validated

        except Exception as e:
            logger.error(f"[NewsProcessor] Ошибка валидации релевантности: {e}. Отклоняем все рынки.")
            return []

    def _extract_markets_from_urls(self, text: str) -> List[Market]:
        """
        Этап 0: Извлекаем прямые ссылки на Polymarket из текста.
        Работает для постов формата 'Top Holder Activity' и других,
        где ссылка polymarket.com/event/SLUG уже содержится в сообщении.
        0 токенов LLM — просто regex + один API-запрос.
        Интеллектуально сортирует найденные рынки, отдавая приоритет тем,
        которые наиболее точно соответствуют тексту новости.
        """
        pattern = r'https?://(?:www\.)?polymarket\.com/(?:event|market)/([A-Za-z0-9_-]+)'
        slugs = list(dict.fromkeys(re.findall(pattern, text)))  # уникальные, порядок сохранён
        if not slugs:
            return []
        markets = []
        seen_ids = set()
        for slug in slugs:
            try:
                found = self.adapter.get_event_by_slug(slug)
                if not found:
                    continue
                    
                # Интеллектуальный скоринг рынков для приоритизации нужных рынков в группе
                def score_market(m, _slug=slug):
                    score = 0
                    # Очищаем название рынка от YES/NO цен в скобках на конце
                    clean_title = re.sub(r'\s*\([^)]*\)\s*$', '', m.title).strip().lower()
                    clean_text = text.lower()
                    if clean_title in clean_text:
                        score += 1000
                    # Дополнительно: считаем пересечение длинных слов (>= 3 символов)
                    words_title = set(w for w in re.findall(r'[a-z0-9]+', clean_title) if len(w) >= 3)
                    words_text = set(w for w in re.findall(r'[a-z0-9]+', clean_text) if len(w) >= 3)
                    score += len(words_title.intersection(words_text)) * 10
                    # Если URL-slug содержится в url самого рынка
                    if _slug.lower() in m.url.lower():
                        score += 50
                    # Дополнительный приоритет активным рынкам в неопределенной зоне
                    if 0.01 < m.price < 0.99:
                        score += 5
                    return score
                    
                # Предвычисляем оценки релевантности для избежания повторных вычислений при логировании
                market_scores = {m.id: score_market(m) for m in found}
                sorted_found = sorted(found, key=lambda m: market_scores[m.id], reverse=True)
                
                for m in sorted_found:
                    if m.id not in seen_ids:
                        seen_ids.add(m.id)
                        markets.append(m)
                        logger.info(
                            f"[NewsProcessor] Этап 0: найден рынок '{m.title}' по slug '{slug}' "
                            f"(score: {market_scores[m.id]}, price: {m.price})"
                        )
            except Exception as e:
                logger.debug(f"[NewsProcessor] Этап 0: ошибка для slug '{slug}': {e}")
        return markets

    def find_relevant_markets(self, text: str) -> List[Market]:
        """
        Анализирует текст новости и ищет подходящие рынки.

        Этап 0: Извлечение прямых polymarket.com ссылок (быстро, 0 токенов LLM)
        Этап 1: LLM → ключевые слова → Polymarket search API → список кандидатов
        Этап 2: LLM → валидация релевантности кандидатов к тексту новости

        Этапы 1-2 запускаются только если Этап 0 не дал результатов.
        Возвращает список объектов Market.
        """
        # ── Этап 0: Прямые ссылки на Polymarket ──────────────────────────────
        direct_markets = self._extract_markets_from_urls(text)
        if direct_markets:
            logger.info(
                f"[NewsProcessor] Этап 0 завершён: найдено {len(direct_markets)} рынков "
                f"по прямым ссылкам. LLM-этапы пропущены."
            )
            return direct_markets

        # ── Этапы 1-2: LLM-пайплайн (fallback если нет прямых ссылок) ───────
        prompt = f"""
Ты — финансовый аналитик. Прочитай следующий пост из новостного канала и выдели 1-3 самых релевантных ключевых слова или коротких фраз на английском языке, по которым можно найти связанные рынки предсказаний (на платформе Polymarket).

Текст поста:
"{text}"

Твоя задача — вернуть ТОЛЬКО JSON с массивом строк "keywords". 
Если пост — просто спам или не содержит конкретной фактуры для ставок, верни пустой массив.

Пример:
Текст: "США планируют ввести санкции против TSMC к концу 2026 года."
Ответ: {{"keywords": ["TSMC", "US sanctions"]}}
"""

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "systemInstruction": {
                "parts": [{"text": "Ты — финансовый аналитик. Отвечай строго в JSON. Keywords возвращай только на английском языке."}]
            },
            "generationConfig": {
                "responseMimeType": "application/json",
            }
        }
        
        result, _ = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="NEXUS_NEWS"
        )
        
        if not result:
            logger.warning("[NewsProcessor] Ошибка LLM при извлечении ключевых слов.")
            return []
            
        try:
            content = extract_response_text(result)
            data = json.loads(content)
            keywords = data.get("keywords", [])
            
            if not keywords:
                logger.info("[NewsProcessor] Новость не содержит релевантных ключевых слов.")
                return []
                
            logger.info(f"[NewsProcessor] Этап 1: Извлечены ключевые слова: {keywords}")
            
            # Этап 1: Поиск кандидатов по ключевым словам
            all_found = []
            seen_ids = set()
            
            for kw in keywords:
                markets = self.adapter.search_markets(kw, limit=3)
                logger.info(
                    f"[NewsProcessor]   '{kw}' → {len(markets)} рынков: "
                    f"{[m.title[:60] for m in markets]}"
                )
                for m in markets:
                    if m.id not in seen_ids and len(all_found) < 10:
                        seen_ids.add(m.id)
                        all_found.append(m)

            if not all_found:
                logger.info("[NewsProcessor] Polymarket API не вернул рынков по ключевым словам.")
                return []

            logger.info(
                f"[NewsProcessor] Этап 1 завершён: {len(all_found)} уникальных кандидатов. "
                f"Запускаем валидацию релевантности..."
            )

            # Этап 2: LLM-валидация релевантности
            validated = self._validate_relevance(text, all_found)

            if not validated:
                logger.warning(
                    f"[NewsProcessor] Этап 2: ни один из {len(all_found)} кандидатов "
                    f"не прошёл валидацию релевантности. Рынки отклонены: "
                    f"{[m.title[:60] for m in all_found]}"
                )
            else:
                logger.info(
                    f"[NewsProcessor] Этап 2 завершён: {len(validated)} рынков прошли валидацию."
                )

            return validated
            
        except Exception as e:
            logger.error(f"[NewsProcessor] Ошибка обработки ответа LLM: {e}")
            return []
