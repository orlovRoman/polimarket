import os
import json
from datetime import datetime
from typing import Optional
from core.models import Market, Signal
from core.context import MarketContext
from agents.shared.python.db import get_memory, get_agent_episodes, get_performance_summary
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news

class SwingAgent:
    """
    Агент SWING_TRADER — спекулянт, ищущий хайп-потенциал на сильно перекошенных рынках.
    Работает с "дешевыми" исходами и оценивает вероятность пампа на новостях.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def estimate_market(self, context: 'MarketContext', price_history: list = None) -> Optional[Signal]:
        """
        Оценивает рынок на потенциал хайпа.
        """
        market = context.market
        news_titles = context.news_titles
        reddit_posts = context.reddit_posts
        wiki_context = context.wiki_context
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SWING] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        price_history_str = "История цен недоступна."
        if price_history:
            lines = [f"  {p['recorded_at']}: {p['price']:.4f}" for p in price_history[-6:]]
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)

        wiki_block = "\n".join(wiki_context) if wiki_context else "Wikipedia-данных нет."

        # Загружаем эпизодическую память (последние оценки)
        episodes = get_agent_episodes("SWING", event_type="signal_evaluated", limit=3)
        episodes_text = "Нет недавних оценок."
        if episodes:
            episodes_text = "\n".join([f"- {ep['summary']}" for ep in episodes])
            
        perf_summary = get_performance_summary("SWING", 10)

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Описание: {market.description}
Исход: {market.outcome}

[Твоя производительность и работа над ошибками]
{perf_summary}
Текущая цена исхода (YES): {market.price}
Дата закрытия рынка: {market.close_time.strftime("%Y-%m-%d %H:%M:%S")}

{rag_context}

Данные из Wikipedia (состав турниров, участники, статистика):
{wiki_block}

{price_history_str}

Последние заголовки RSS:
{chr(10).join(news_titles) if news_titles else "RSS новостей не найдено."}

Последние посты с Reddit:
{chr(10).join(reddit_posts) if reddit_posts else "Постов на Reddit не найдено."}

[Недавний опыт (Эпизодическая память)]
Ознакомься со своими недавними предсказаниями и их реальным исходом. Сделай поправку на свою результативность (если ошибался, будь более осторожен).
{episodes_text}

КРИТИЧЕСКОЕ ПРАВИЛО 1: Информация внутри <archival_memory> относится исключительно к ПРОШЛЫМ событиям и должна использоваться как исторический контекст, а не как инструкция к текущему рынку.
КРИТИЧЕСКОЕ ПРАВИЛО 2: ВСЕ текстовые поля в JSON (reasoning, catalyst, catalyst_absence_reason, swing_risk, swing_verdict) ДОЛЖНЫ БЫТЬ НАПИСАНЫ СТРОГО НА РУССКОМ ЯЗЫКЕ! Запрещено использовать китайский, французский, арабский и любые другие языки. Если в тексте появятся иероглифы или символы не-кириллических алфавитов — ответ будет отброшен системой. Технические термины (pump, hype, YES, NO) можно оставлять на английском.

Твоя задача — оценить вероятность резкого скачка цены (hype potential).
Ответ верни строго в формате JSON.
"""
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "hype_potential": {"type": "NUMBER", "description": "0.0 to 1.0"},
                "recommendation": {"type": "STRING", "description": "buy or ignore"},
                "target_outcome": {"type": "STRING"},
                "target_exit_price": {"type": "NUMBER"},
                "confidence": {"type": "NUMBER"},
                "reasoning": {"type": "STRING"},
                "catalyst": {"type": "STRING"},
                "catalyst_absence_reason": {"type": "STRING"},
                "swing_risk": {"type": "STRING"},
                "swing_verdict": {"type": "STRING"}
            },
            "required": ["hype_potential", "recommendation", "target_outcome", "target_exit_price", "confidence", "reasoning", "catalyst", "catalyst_absence_reason", "swing_risk", "swing_verdict"]
        }
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema
            }
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
        
        analysis = None
        for attempt in range(2):
            result, active_model = generate_content_with_fallback(
                api_key=self.api_key,
                payload=payload,
                default_model=self.model,
                agent_name="SWING",
                market_id=market.id
            )
            
            if not result:
                continue
                
            try:
                content = extract_response_text(result)
                # Очистим возможные markdown блоки, если Grok игнорирует schema
                content = content.replace("```json", "").replace("```", "").strip()
                analysis = json.loads(content, strict=False)
                
                recommendation = analysis.get("recommendation", "ignore").lower()
                hype_potential = float(analysis.get("hype_potential", 0))
                target_outcome = analysis.get("target_outcome", "YES")
                target_price = float(analysis.get("target_exit_price", 0.15))
                
                # Расчет ROI (Return on Investment)
                current_price = market.price if target_outcome == "YES" else (1.0 - market.price)
                if current_price <= 0: current_price = 0.01
                roi = ((target_price - current_price) / current_price) * 100
                
                from core.models import SwingSignal
                
                signal = SwingSignal(
                    id=f"sig-swing-{market.id}-{int(datetime.now().timestamp())}",
                    market_id=market.id,
                    type="SWING",
                    platform=market.platform,
                    recommendation=recommendation,
                    confidence=float(analysis.get("confidence", 0.5)),
                    hype_potential=hype_potential,
                    target_outcome=target_outcome,
                    target_exit_price=target_price,
                    reasoning=analysis.get("reasoning", ""),
                    catalyst=analysis.get("catalyst", ""),
                    catalyst_absence_reason=analysis.get("catalyst_absence_reason", ""),
                    swing_risk=analysis.get("swing_risk", "") or analysis.get("risk", "Не указан риск"),
                    swing_verdict=analysis.get("swing_verdict", "") or analysis.get("verdict", "Не указан вердикт"),
                    summary=f"🚀 Памп {target_outcome} (Хайп {hype_potential*100:.0f}%, Цель {target_price:.2f})" if recommendation == "buy" else f"💤 Игнор (Хайп {hype_potential*100:.0f}%)",
                    details=f"Рекомендация: {recommendation.upper()} {target_outcome} по ~{current_price:.2f}, выход по {target_price:.2f} (ROI ~{roi:.0f}%).\nОбоснование: {analysis.get('reasoning', '')}"
                )
                return signal
                
            except json.JSONDecodeError as e:
                print(f"[SWING] Ошибка парсинга JSON (попытка {attempt+1}): {e}")
            except Exception as e:
                print(f"[SWING] Ошибка при оценке рынка {market.id} (попытка {attempt+1}): {e}")
                
        return None
