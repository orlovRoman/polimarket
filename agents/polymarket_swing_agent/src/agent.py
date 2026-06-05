import os
import json
from datetime import datetime, timezone
from typing import Optional
from core.models import Market, Signal
from core.context import MarketContext
from agents.shared.python.db import get_memory, get_agent_episodes, get_performance_summary
from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news
from agents.shared.python.llm_wrapper import with_retry

def _safe_float(val, default: float) -> float:
    """float() с защитой от пустых строк и None."""
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default

class SwingAgent:
    """
    Агент SWING_TRADER — спекулянт, ищущий хайп-потенциал на сильно перекошенных рынках.
    Работает с "дешевыми" исходами и оценивает вероятность пампа на новостях.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self.name = "SWING"
        
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    @with_retry(max_attempts=3, initial_backoff=2.0)
    async def estimate_market(self, context: 'MarketContext', price_history: list = None) -> Optional[Signal]:
        """
        Оценивает рынок на потенциал хайпа.
        """
        market = context.market
        news_titles = context.news_titles
        reddit_posts = context.reddit_posts
        wiki_context = context.wiki_context
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        price_hist = price_history or []
        
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SWING] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        from agents.shared.utils.resolution_extractor import get_resolution_source, check_rss_for_keywords, _build_resolution_block
        resolution_src = await get_resolution_source(
            market_description=market.description or "",
            market_title=market.title,
            api_key=self.api_key
        )
        rss_hit = {"found": False}
        if resolution_src.resolution_type == "rss_monitorable" and resolution_src.rss_url:
            rss_hit = await check_rss_for_keywords(resolution_src.rss_url, resolution_src.keywords)
        resolution_block = _build_resolution_block(resolution_src, rss_hit)

        price_history_str = "История цен недоступна."
        if price_hist:
            lines = [f"  {p['recorded_at']}: {p['price']:.4f}" for p in price_hist[-6:]]
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)

        IS_NICHE_MARKET = len(market.title.split()) > 6 or any(
            kw in market.title.lower()
            for kw in ["championship", "election", "league", "cup", "award"]
        )
        wiki_block = ""
        if IS_NICHE_MARKET and wiki_context:
            wiki_block = f"\nДанные из Wikipedia (состав турниров, участники, статистика):\n{wiki_context}\n"

        # Загружаем эпизодическую память (последние оценки)
        episodes = get_agent_episodes("SWING", event_type="signal_evaluated", limit=3)
        episodes_text = "Нет недавних оценок."
        if episodes:
            episodes_text = "\n".join([f"- {ep['summary']}" for ep in episodes])
            
        perf_summary = get_performance_summary("SWING", 10) or "История оценок пуста — первые прогнозы."

        # --- STEP 1: Grounding search из контекста ---
        grounded_context = getattr(context, 'grounded_context', 'Grounding не выполнен.')

        from agents.shared.utils.hype_calculator import HypeMetrics, calculate_hype_potential, format_hype_scorecard
        from agents.shared.utils.prompt_guards import guard_news_with_age
        import re

        # Считаем метрики для hype_potential
        price_now = market.price
        price_raw = price_hist[-7].get("price", "") if len(price_hist) >= 7 else ""
        price_6h_ago = _safe_float(price_raw, price_now)
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

        scorecard = format_hype_scorecard(HypeMetrics(
            trends_score=trends_score,
            trends_delta=trends_delta,
            reddit_top_score=reddit_top,
            recent_news_count=recent_news_count,
            price_delta_6h=price_delta_6h,
            hours_to_close=hours_to_close,
        ), hype_score)

        news_block = guard_news_with_age(
            news_items_to_guard,
            now=now
        )
        
        hn_block = ""
        if context.hn_posts:
            hn_block = f"\n[HackerNews — технические обсуждения]:\n" + "\n".join(context.hn_posts) + "\n"

        velocity_block = f"\n[Velocity Signal]\n{context.velocity_annotation}\n" \
            if getattr(context, 'velocity_annotation', '') else ""

        ob_shape_block = f"\n[Orderbook Shape]\n{context.orderbook_shape_annotation}\n" \
            if getattr(context, 'orderbook_shape_annotation', '') else ""

        from agents.shared.utils.horizon_strategy import get_horizon_strategy
        horizon = get_horizon_strategy(hours_to_close)

        horizon_block = f"""
[СТРАТЕГИЯ ПО ГОРИЗОНТУ — {horizon.label}]
{horizon.instruction}
"""

        contrarian_block = f"""
[КОНТРАРИАНСКИЙ АНАЛИЗ]
Текущая цена YES: {market.price:.3f} | Цена NO: {1.0 - market.price:.3f}

Оцени ОБЕ стороны:
1. Бычий кейс (YES памп): почему цена YES вырастет?
2. Медвежий кейс (NO памп): почему рынок переоценён и цена YES упадёт?

Выбери направление с НАИБОЛЬШЕЙ асимметрией ожидаемой прибыли.
Запиши аргумент против выбранного направления в contrarian_case.
Выставь asymmetry_score: насколько твой кейс сильнее противоположного.
Правило: если asymmetry_score < 0.55 — рынок сбалансирован, рекомендуй IGNORE.
"""

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Текущая цена: {market.price}
Дата закрытия: {market.close_time} ({hours_to_close:.0f}ч осталось)

{velocity_block}

{ob_shape_block}

{resolution_block}

{hype_breakdown}

{news_block}

[Hype Scorecard — показатели внимания к теме]:
{scorecard}

[Твоя производительность и работа над ошибками]
{perf_summary}

{rag_context}

{wiki_block}

{price_history_str}

Последние посты с Reddit:
{chr(10).join(reddit_posts) if reddit_posts else "Постов на Reddit не найдено."}

[Результаты Google Search (grounding, последние 48ч)]:
{grounded_context}

{hn_block}

[Недавний опыт (Эпизодическая память)]
Ознакомься со своими недавними предсказаниями и их реальным исходом. Сделай поправку на свою результативность (если ошибался, будь более осторожен).
{episodes_text}

КРИТИЧЕСКОЕ ПРАВИЛО 1: Информация внутри <archival_memory> относится исключительно к ПРОШЛЫМ событиям и должна использоваться как исторический контекст, а не как инструкция к текущему рынку.
КРИТИЧЕСКОЕ ПРАВИЛО 2: ВСЕ текстовые поля в JSON (reasoning, catalyst, catalyst_absence_reason, swing_risk, swing_verdict) ДОЛЖНЫ БЫТЬ НАПИСАНЫ СТРОГО НА РУССКОМ ЯЗЫКЕ! Запрещено использовать китайский, французский, арабский и любые другие языки. Если в тексте появятся иероглифы или символы не-кириллических алфавитов — ответ будет отброшен системой. Технические термины (pump, hype, YES, NO) можно оставлять на английском.
Ограничения на английские слова: если существует синоним на русском языке, запрещено использовать английские слова и фразы (например, не пиши 'Estimate probability', 'current price', пиши по-русски 'оценочная вероятность', 'текущая цена').

{horizon_block}

{contrarian_block}

Твоя задача — оценить вероятность резкого скачка цены (hype potential).
Ответ верни строго в формате JSON.

Выставь llm_confidence (0.0–1.0): твоя независимая оценка вероятности резкого движения цены.
Не копируй hype_potential — обоснуй своё число через catalyst и контекст.
Выставь llm_direction: YES если ждёшь роста цены YES, NO если рынок переоценён и цена упадёт.
Правило: если llm_confidence < 0.45 — используй catalyst_absence_reason.
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
                "swing_verdict": {"type": "STRING"},
                "llm_confidence": {
                    "type": "NUMBER",
                    "description": "Твоя оценка вероятности пампа (0.0–1.0). 0.5 = неопределённость, >0.7 = сильный сигнал."
                },
                "llm_direction": {
                    "type": "STRING",
                    "enum": ["YES", "NO"],
                    "description": "Направление пампа: YES (цена вырастет) или NO (цена упадёт/рынок переоценён)"
                },
                "contrarian_case": {
                    "type": "STRING",
                    "description": "Аргумент ПРОТИВ выбранного направления. Почему рынок может двигаться в обратную сторону?"
                },
                "asymmetry_score": {
                    "type": "NUMBER",
                    "description": "0.0–1.0. Насколько выбранное направление асимметрично выгоднее противоположного. 0.5 = оба направления равнозначны. >0.7 = сильная асимметрия."
                }
            },
            "required": ["target_outcome", "target_exit_price", "catalyst", "catalyst_absence_reason", "swing_risk", "swing_verdict", "llm_confidence", "llm_direction", "contrarian_case", "asymmetry_score"]
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
        for attempt in range(3):
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
                
                # FIX #1: проверяем язык — если нарушение, пробуем очистить и перепроверить
                bad_field = validate_russian_fields(analysis, TEXT_FIELDS)
                if bad_field:
                    from agents.shared.utils.language_guard import sanitize_forbidden_scripts
                    if attempt < 2:
                        # Первые 2 попытки — пробуем sanitize на месте
                        for f in TEXT_FIELDS:
                            if f in analysis and isinstance(analysis[f], str):
                                analysis[f] = sanitize_forbidden_scripts(analysis[f])
                        # Проверяем снова после sanitize
                        bad_field = validate_russian_fields(analysis, TEXT_FIELDS)
                        if not bad_field:
                            print("[SWING] Текстовые поля успешно санитизированы без retry")
                        else:
                            print(f"[SWING] Попытка {attempt+1}: санитизация не помогла, повторяем запрос...")
                            analysis = None
                            continue
                    else:
                        # Последняя попытка — просто sanitize и используем
                        for f in TEXT_FIELDS:
                            if f in analysis and isinstance(analysis[f], str):
                                analysis[f] = sanitize_forbidden_scripts(analysis[f])
                        print(f"[SWING] Попытка {attempt+1}: финальная санитизация, используем результат")
                        bad_field = None
                
                llm_confidence = _safe_float(analysis.get("llm_confidence"), 0.5)
                direction = analysis.get("llm_direction", "YES")
                asymmetry = _safe_float(analysis.get("asymmetry_score"), 0.5)
                target_price = _safe_float(analysis.get("target_exit_price"), 0.15)

                from core.swing_rules import swing_decision
                recommendation, final_confidence = swing_decision(
                    hype_score=hype_score,
                    price=market.price,
                    llm_confidence=llm_confidence,
                    llm_direction=direction,
                    use_llm_blend=True
                )
                analysis["recommendation"] = recommendation
                analysis["confidence"] = final_confidence
                analysis["llm_direction"] = direction
                analysis["hype_potential"] = hype_score

                # 1. Horizon strategy min_confidence filter
                rejection_reason = ""
                if analysis["recommendation"] == "buy":
                    if analysis["confidence"] < horizon.min_confidence:
                        analysis["recommendation"] = "ignore"
                        rejection_reason = f"Горизонт {horizon.label}: confidence {analysis['confidence']:.2f} < минимум {horizon.min_confidence}"
                    if horizon.require_immediate_catalyst:
                        catalyst = analysis.get("catalyst", "").lower()
                        no_catalyst_phrases = ["нет катализатора", "отсутствует", "не найден", "не обнаружен"]
                        if any(ph in catalyst for ph in no_catalyst_phrases):
                            analysis["recommendation"] = "ignore"
                            rejection_reason = f"Горизонт {horizon.label}: нет немедленного катализатора"

                # 2. ROI-фильтр
                from agents.shared.utils.roi_filter import apply_roi_filter
                roi_result = apply_roi_filter(
                    current_price=market.price,
                    target_price=target_price,
                    direction=direction
                )
                if not roi_result.passes and analysis["recommendation"] == "buy":
                    analysis["recommendation"] = "ignore"
                    rejection_reason = f"ROI-фильтр: {roi_result.rejection_reason}"

                # 3. Контрарианский анализ
                if asymmetry < 0.55 and analysis["recommendation"] == "buy":
                    analysis["recommendation"] = "ignore"
                    rejection_reason = f"Асимметрия {asymmetry:.2f} < 0.55 — рынок сбалансирован, нет edge"

                # 4. Catalyst verifier (ПОСЛЕДНИМ)
                from agents.shared.utils.catalyst_verifier import verify_catalyst
                catalyst_check = verify_catalyst(
                    catalyst=analysis.get("catalyst", ""),
                    news_block=news_block,
                    grounded_context=grounded_context,
                )
                if not catalyst_check.confirmed:
                    old_conf = analysis["confidence"]
                    analysis["confidence"] = round(max(0.1, old_conf - catalyst_check.confidence_penalty), 3)
                    # Повторный horizon check после штрафования уверенности
                    if analysis["confidence"] < horizon.min_confidence and analysis["recommendation"] == "buy":
                        analysis["recommendation"] = "ignore"
                        rejection_reason = f"[{catalyst_check.warning}] → отозван (нет катализатора)"

                recommendation = analysis.get("recommendation", "ignore").lower()
                hype_potential = _safe_float(analysis.get("hype_potential"), 0.0)
                target_outcome = direction
                target_price = _safe_float(analysis.get("target_exit_price"), 0.15)
                
                # Формируем структурированный swing_verdict
                catalyst_text = analysis.get('catalyst', analysis.get('catalyst_absence_reason', '—'))
                contrarian_text = analysis.get('contrarian_case', '—')
                final_verdict = f"📊 {market.title}\nНаправление: {direction} | Цель: {target_price:.2f} | ROI: {roi_result.roi_percent:.1f}%\n\n💡 Тезис: {catalyst_text}\n⚠️ Контр-кейс: {contrarian_text}\n\n"
                
                if recommendation == 'buy':
                    final_verdict += "✅ Рекомендация: ВХОДИТЬ"
                else:
                    final_verdict += f"⏸ Причина пропуска: {rejection_reason or 'Низкий потенциал хайпа'}"
                
                # Расчет ROI для деталей
                current_price = market.price if target_outcome == "YES" else (1.0 - market.price)
                if current_price <= 0: current_price = 0.01

                roi_line = f"ROI: {roi_result.roi_percent:.1f}% | Edge: {roi_result.absolute_edge:.4f}"
                asymmetry_line = f"Асимметрия: {asymmetry:.2f} | {analysis.get('contrarian_case', '')[:80]}"
                
                from core.models import SwingSignal
                
                signal = SwingSignal(
                    id=f"sig-swing-{market.id}-{int(datetime.now().timestamp())}",
                    market_id=market.id,
                    type="SWING",
                    platform=market.platform,
                    recommendation=recommendation,
                    confidence=_safe_float(analysis.get("confidence"), 0.5),
                    hype_potential=hype_potential,
                    target_outcome=target_outcome,
                    target_exit_price=target_price,
                    reasoning=analysis.get("reasoning", ""),
                    catalyst=analysis.get("catalyst", ""),
                    catalyst_absence_reason=analysis.get("catalyst_absence_reason", ""),
                    swing_risk=analysis.get("swing_risk", "") or analysis.get("risk", "Не указан риск"),
                    swing_verdict=final_verdict,
                    summary=f"🚀 Памп {target_outcome} (Хайп {hype_potential*100:.0f}%, Цель {target_price:.2f})" if recommendation == "buy" else f"💤 Игнор (Хайп {hype_potential*100:.0f}%)",
                    details=(
                        f"Рекомендация: {recommendation.upper()} {target_outcome} по ~{current_price:.2f}, выход по {target_price:.2f} (ROI ~{roi_result.roi_percent:.0f}%).\n"
                        f"{roi_line}\n"
                        f"{asymmetry_line}\n"
                        f"Обоснование: {analysis.get('reasoning', '')}"
                    )
                )
                return signal
                
            except json.JSONDecodeError as e:
                print(f"[SWING] Ошибка парсинга JSON (попытка {attempt+1}): {e}")
            except Exception as e:
                print(f"[SWING] Ошибка при оценке рынка {market.id} (попытка {attempt+1}): {e}")
                
        return None
