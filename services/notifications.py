# services/notifications.py
"""
Единый сервис Telegram-уведомлений.
Все отправки в Telegram идут только через этот модуль.
"""
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger


def _convert_reply_markup(reply_markup):
    if not reply_markup:
        return None
    # aiogram 3 / Pydantic v2
    if hasattr(reply_markup, "model_dump"):
        return reply_markup.model_dump(exclude_none=True)
    # aiogram 3 / Pydantic v1 fallback
    if hasattr(reply_markup, "dict"):
        return reply_markup.dict(exclude_none=True)
    # Уже готовый словарь
    if isinstance(reply_markup, dict):
        return reply_markup
    return None


def send_telegram(text: str, parse_mode: str = "HTML", reply_markup = None) -> bool:
    """Базовая отправка сообщения. Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[Notifier] TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        converted_markup = _convert_reply_markup(reply_markup)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if converted_markup:
            payload["reply_markup"] = converted_markup
            
        resp = requests.post(url, json=payload, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            fallback_payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            }
            if converted_markup:
                fallback_payload["reply_markup"] = converted_markup
            resp = requests.post(url, json=fallback_payload, timeout=10)
            
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки: {e}")
        return False


def send_telegram_to_chat(text: str, chat_id: str, parse_mode: str = "HTML", reply_markup = None) -> bool:
    """Отправка сообщения в конкретный чат (используется для event-driven). Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        logger.warning(f"[Notifier] TELEGRAM_BOT_TOKEN или chat_id ({chat_id}) не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        converted_markup = _convert_reply_markup(reply_markup)
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if converted_markup:
            payload["reply_markup"] = converted_markup
            
        resp = requests.post(url, json=payload, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            fallback_payload = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            }
            if converted_markup:
                fallback_payload["reply_markup"] = converted_markup
            resp = requests.post(url, json=fallback_payload, timeout=10)
            
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки в чат {chat_id}: {e}")
        return False

def send_correlation_alerts(summary_callback=None) -> None:
    """Анализирует новые корреляции на наличие кросс-рыночного арбитража."""
    from agents.shared.python.db import get_new_correlations, mark_correlations_notified
    from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
    from agents.shared.adapters.polymarket import PolymarketAdapter
    import os

    notify = summary_callback or send_telegram
    new_corrs = []
    processed_ids = []
    try:
        new_corrs = get_new_correlations()
        logger.info(f"[Notifier] Корреляций для обработки: {len(new_corrs)}")
        if not new_corrs:
            return

        adapter = PolymarketAdapter()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.error("[Notifier] Нет API ключа для Арбитражника.")
            return

        for c in new_corrs[:5]:
            try:
                # Получаем свежие данные о рынках
                market_a = adapter.get_market(c['market_id_a'])
                market_b = adapter.get_market(c['market_id_b'])

                if not market_a or not market_b:
                    processed_ids.append(c['id'])
                    continue

                arbitrage_agent = ArbitrageAgent(api_key=api_key, model="gemini-2.5-flash")
                signal = arbitrage_agent.analyze_correlation(
                    market_a=market_a,
                    market_b=market_b,
                    correlation_type=c.get('correlation_type', 'thematic'),
                    score=int(c.get('confidence', 0) * 100)
                )

                logger.debug(f"[Notifier] signal={signal}, has_arbitrage={getattr(signal,'has_arbitrage',None)}, spread={getattr(signal,'spread_percent',None)}")

                if signal:
                    spread = getattr(signal, 'spread_percent', 0.0)
                    platform_a = getattr(market_a, "platform", "Polymarket").upper()
                    platform_b = getattr(market_b, "platform", "Polymarket").upper()

                    if signal.has_arbitrage:
                        header = f"🚨 <b>ПОДТВЕРЖДЁННЫЙ АРБИТРАЖ ({platform_a} ↔ {platform_b})</b> 🚨"
                    elif spread is not None and spread >= 5.0 and getattr(signal, 'arbitrage_type', 'none') != 'none':
                        header = f"⚠️ <b>ПОТЕНЦИАЛЬНАЯ ВОЗМОЖНОСТЬ ({platform_a} ↔ {platform_b})</b>"
                    else:
                        logger.info(
                            f"[Notifier] Корреляция {c['id']}: пропущена (has_arbitrage=False, "
                            f"spread={spread:.1f}%, "
                            f"reason={getattr(signal,'reasoning','')[:80]})"
                        )
                        processed_ids.append(c['id'])
                        continue

                    alert_text = (
                        f"{header}\n\n"
                        f"📍 <b>Рынок A ({platform_a}):</b> <a href='{market_a.url}'>{market_a.title}</a> (Цена: {market_a.price})\n"
                        f"📍 <b>Рынок B ({platform_b}):</b> <a href='{market_b.url}'>{market_b.title}</a> (Цена: {market_b.price})\n\n"
                        f"💡 <b>Тип:</b> {signal.arbitrage_type}\n"
                        f"📈 <b>Разрыв (Spread):</b> {spread}%\n\n"
                        f"🧠 <b>Логика:</b> {signal.reasoning}\n\n"
                        f"⚡ <b>Трейд:</b> {signal.trade_instruction}\n"
                    )
                    notify(alert_text)
                else:
                    logger.info(f"[Notifier] Корреляция {c['id']}: агент вернул None")
                processed_ids.append(c['id'])
            except Exception as item_err:
                logger.error(f"[Notifier] Ошибка обработки корреляции {c['id']}: {item_err}", exc_info=True)
                processed_ids.append(c['id'])
                continue
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки корреляций: {e}")
    finally:
        if processed_ids:
            mark_correlations_notified(processed_ids)


# ─── Кросс-платформенный арбитраж (Polymarket ↔ Kalshi и др.) ──────────────

ARBITRAGE_TYPE_LABELS = {
    "price_divergence":      "💰 Прямое ценовое расхождение",
    "logical_contradiction": "🧠 Логическое противоречие",
    "pair_trade":            "🔗 Парный трейд",
}


# Алерты для этих трех стратегий удалены

async def send_compound_alert(bot, chat_id: int, opp) -> None:
    """Отправляет алерт о Favourite Compounding возможности с inline-кнопками."""
    from agents.shared.python.db import get_notification_settings
    if not get_notification_settings().get("notify_favourite_compounding", True):
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    hours = opp.hours_left
    time_str = f"{hours:.1f}ч" if hours >= 1 else f"{hours*60:.0f}мин"
    price_cents = int(round(opp.price * 100))
    outcome = getattr(opp, "outcome", "YES")

    try:
        spread_val = float(opp.spread_pct or 0.0)
    except (TypeError, ValueError):
        spread_val = 0.0

    text = (
        f"💰 <b>FAVOURITE COMPOUNDING</b>\n\n"
        f"📍 <b>{opp.title[:100]}...</b>\n\n"
        f"🎯 Исход: <b>{outcome}</b>\n"
        f"💵 Цена {outcome}: <b>{price_cents}¢</b>  "
        f"📈 ROI: <b>+{opp.roi_net_pct:.2f}%</b>\n"
        f"⏱ До закрытия: <b>{time_str}</b>  "
        f"📊 Объём: <b>${opp.volume_usd:,.0f}</b>\n"
        f"🎯 Уверенность: <b>{opp.confidence*100:.0f}%</b>\n"
        f"🔍 <i>{opp.obviousness_reason}</i>\n\n"
        f"📌 Spread: {spread_val*100:.2f}%"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"✅ Купить {outcome} ({price_cents}¢)",
            callback_data=f"compound_buy:{opp.opp_id}"
        ),
        InlineKeyboardButton(
            text="🔍 Проанализировать",
            callback_data=f"cmp_ana_a:{opp.market_id[:40]}"
        ),
        InlineKeyboardButton(
            text="🔗 Открыть",
            url=opp.url
        ),
    ]])

    await bot.send_message(
        chat_id, text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )

async def send_compound_exit_alert(bot, chat_id: int, opp, current_price: float) -> None:
    """Отправляет алерт о возможности досрочного закрытия Favourite Compounding позиции (профи-продажа)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Безопасное приведение цены покупки
    try:
        entry_price = float(opp.get("price") or 0.0)
    except (TypeError, ValueError):
        entry_price = 0.0
    if entry_price <= 0.0:
        entry_price = 0.01

    price_cents = int(round(current_price * 100))
    init_cents = int(round(entry_price * 100))
    outcome = opp.get("outcome", "YES")
    
    # Считаем ROI
    from services.favourite_compounder import ROICalculator
    from agents.shared.python.db import get_compound_settings
    cfg = get_compound_settings()
    virtual_stake = cfg.get("virtual_stake", 50.0)
    pnl = virtual_stake * (current_price - entry_price) / entry_price * (1.0 - ROICalculator.POLY_FEE_PCT)

    text = (
        f"💎 <b>EXIT: ПРОФИ-ПРОДАЖА (Favourite Compounding)</b>\n\n"
        f"📍 <b>{opp['title'][:100]}...</b>\n\n"
        f"🎯 Исход: <b>{outcome}</b>\n"
        f"📈 Текущая цена {outcome} достигла: <b>{price_cents}¢</b> (покупка по {init_cents}¢)\n"
        f"💰 Ожидаемый PnL: <b>+${pnl:.2f}</b>\n"
        f"⚠️ До формальной резолюции UMA осталось совсем немного. Продайте сейчас для высвобождения капитала!"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"✅ Продано по {price_cents}¢",
            callback_data=f"compound_sell:{opp['id'][:36]}:{current_price:.4f}"
        ),
        InlineKeyboardButton(
            text="🔗 Открыть",
            url=opp["url"]
        ),
    ]])

    await bot.send_message(
        chat_id, text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )
