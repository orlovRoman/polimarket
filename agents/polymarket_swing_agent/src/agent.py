import os
import json
from datetime import datetime, timezone
from typing import Optional
from core.models import Market, Signal
from core.context import MarketContext
from agents.shared.python.db import get_memory, get_agent_episodes, get_performance_summary
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news
from agents.shared.python.llm_wrapper import with_retry

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

    @with_retry(max_attempts=3, initial_backoff=2.0)
    def estimate_market(self, context: 'MarketContext', price_history: list = None) -> Optional[Signal]:
        """
        Оценивает рынок на потенциал хайпа.
        """
        market = context.market
        news_titles = context.news_titles
        reddit_posts = context.reddit_posts
        wiki_context = context.wiki_context
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        price_hist = price_history or []
        
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SWING] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        price_history_str = "История цен недоступна."
        if price_hist:
            lines = [f"  {p['recorded_at']}: {p['price']:.4f}" for p in price_hist[-6:]]
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)

        wiki_block = wiki_context or "Wikipedia-данных нет."

        # Загружаем эпизодическую память (последние оценки)
        episodes = get_agent_episodes("SWING", event_type="signal_evaluated", limit=3)
        episodes_text = "Нет недавних оценок."
        if episodes:
            episodes_text = "\n".join([f"- {ep['summary']}" for ep in episodes])
            
        perf_summary = get_performance_summary("SWING", 10) or "История оценок пуста — первые прогнозы."

        # --- STEP 1: Grounding search из контекста ---
        grounded_context = getattr(context, 'grounded_context', 'Grounding не выполнен.')

        from agents.shared.utils.hype_calculator import HypeMetrics, calculate_hype_potential
        from agents.shared.utils.prompt_guards import guard_news_with_age
        import re

        # Считаем метрики для hype_potential
        price_now = market.price
        price_6h_ago = price_hist[-7]["price"] if len(price_hist) >= 7 else price_now
        price_delta_6h = price_now - price_6h_ago

        close_dt = market.close_time
        now_utc = datetime.now(tz=timezone.utc)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        hours_to_close = max((close_dt - now_utc).total_seconds() / 3600, 0)

        # Trends score
        trends_raw = context.trends_data  # строка или число — парсим
        trends_match = re.search(r'\d+', str(trends_raw))
        trends_score = int(trends_match.group()) if trends_match else 0
        trends_delta = 0  # если нет истории Trends

        # Reddit
        reddit_top = 0
        for post in (context.reddit_posts or []):
            score = post.get("score", 0) if isinstance(post, dict) else 0
            reddit_top = max(reddit_top, score)

        # Форматируем новости для guard_news_with_age с датами
        news_items_to_guard = []
        for item in (context.news_titles or []):
            match = re.match(r'^\[([^\]]+)\]\s*(.*)$', item)
            if match:
                date_str = match.group(1)
                title_part = match.group(2)
                iso_date = None
                if date_str != "дата неизвестна":
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%a, %d %b %Y", "%d %b %Y"):
                        try:
                            dt = datetime.strptime(date_str.strip(), fmt)
                            iso_date = dt.isoformat()
                            break
                        except ValueError:
                            continue
                news_items_to_guard.append({"title": title_part, "published": iso_date})
            else:
                news_items_to_guard.append({"title": item, "published": None})

        # Теперь считаем recent_news_count из уже обработанного списка
        recent_news_count = 0
        now = datetime.now(tz=timezone.utc)
        for ni in news_items_to_guard:
            pub = ni.get("published")
            if pub:
                try:
                    pub_dt = datetime.fromisoformat(pub)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    age_h = (now - pub_dt).total_seconds() / 3600
                    if 0 <= age_h <= 6:
                        recent_news_count += 1
                except Exception:
                    pass

        hype_score, hype_breakdown = calculate_hype_potential(HypeMetrics(
            trends_score=trends_score,
            trends_delta=trends_delta,
            reddit_top_score=reddit_top,
            recent_news_count=recent_news_count,
            price_delta_6h=price_delta_6h,
            hours_to_close=hours_to_close,
        ))

        news_block = guard_news_with_age(
            news_items_to_guard,
            now=now
        )

        prompt = f"""
Рынок: {market.title}
Текущая цена: {market.price}
Дата закрытия: {market.close_time} ({hours_to_close:.0f}ч осталось)

{hype_breakdown}

{news_block}

[Твоя производительность и работа над ошибками]
{perf_summary}

{rag_context}

Данные из Wikipedia (состав турниров, участники, статистика):
{wiki_block}

{price_history_str}

Последние посты с Reddit:
{chr(10).join(reddit_posts) if reddit_posts else "Постов на Reddit не найдено."}

[Результаты Google Search (grounding, последние 48ч)]:
{grounded_context}

[Google Trends — уровень интереса к теме]:
{context.trends_data}

[HackerNews — технические обсуждения]:
{chr(10).join(context.hn_posts) if context.hn_posts else "HackerNews: нет релевантных постов."}

[Недавний опыт (Эпизодическая память)]
Ознакомься со своими недавними предсказаниями и их реальным исходом. Сделай поправку на свою результативность (если ошибался, будь более осторожен).
{episodes_text}

КРИТИЧЕСКОЕ ПРАВИЛО 1: Информация внутри <archival_memory> относится исключительно к ПРОШЛЫМ событиям и должна использоваться как исторический контекст, а не как инструкция к текущему рынку.
КРИТИЧЕСКОЕ ПРАВИЛО 2: ВСЕ текстовые поля в JSON (reasoning, catalyst, catalyst_absence_reason, swing_risk, swing_verdict) ДОЛЖНЫ БЫТЬ НАПИСАНЫ СТРОГО НА РУССКОМ ЯЗЫКЕ! Запрещено использовать китайский, французский, арабский и любые другие языки. Если в тексте появятся иероглифы или символы не-кириллических алфавитов — ответ будет отброшен системой. Технические термины (pump, hype, YES, NO) можно оставлять на английском.
Ограничения на английские слова: если существует синоним на русском языке, запрещено использовать английские слова и фразы (например, не пиши 'Estimate probability', 'current price', пиши по-русски 'оценочная вероятность', 'текущая цена').

Твоя задача — оценить вероятность резкого скачка цены (hype potential).
Ответ верни строго в формате JSON.
"""
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "target_outcome": {"type": "STRING"},
                "target_exit_price": {"type": "NUMBER"},
                "reasoning": {"type": "STRING"},
                "catalyst": {"type": "STRING"},
                "catalyst_absence_reason": {"type": "STRING"},
                "swing_risk": {"type": "STRING"},
                "swing_verdict": {"type": "STRING"}
            },
            "required": ["target_outcome", "target_exit_price", "catalyst", "catalyst_absence_reason", "swing_risk", "swing_verdict"]
        }
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema
            }
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback, extract_response_text
        
        from agents.shared.utils.language_guard import validate_russian_fields
        TEXT_FIELDS = ["reasoning", "catalyst", "catalyst_absence_reason", "swing_risk", "swing_verdict", "risk", "verdict"]
        
        analysis = None
        for attempt in range(1):
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
                if not content:
                    continue
                analysis = json.loads(content, strict=False)
                
                # FIX #1: проверяем язык — если нарушение, повторяем запрос
                bad_field = validate_russian_fields(analysis, TEXT_FIELDS)
                if bad_field:
                    print(f"[SWING] Попытка {attempt+1}: поле '{bad_field}' содержит запрещённые символы, повторяем запрос...")
                    analysis = None
                    continue
                
                from core.swing_rules import swing_decision
                recommendation, confidence = swing_decision(hype_score, market.price)
                analysis["recommendation"] = recommendation
                analysis["confidence"] = confidence
                analysis["hype_potential"] = hype_score

                # Гард: проверяем обоснование target_exit_price в swing_verdict
                exit_price = analysis.get("target_exit_price")
                verdict = analysis.get("swing_verdict", "") or analysis.get("verdict", "")
                if exit_price and str(exit_price) not in verdict:
                    print(
                        f"[SWING] target_exit_price={exit_price} не упомянут в swing_verdict. "
                        f"Обоснование отсутствует."
                    )
                    analysis["swing_verdict"] = (
                        f"{verdict} [⚠️ целевая цена {exit_price} не обоснована в тексте]"
                    )

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
