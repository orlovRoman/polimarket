# services/notifications.py
"""
Единый сервис Telegram-уведомлений.
Все отправки в Telegram идут только через этот модуль.
"""
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger


def send_telegram(text: str, parse_mode: str = "HTML", reply_markup: dict = None) -> bool:
    """Базовая отправка сообщения. Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[Notifier] TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
            
        resp = requests.post(url, json=payload, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            fallback_payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            }
            if reply_markup:
                fallback_payload["reply_markup"] = reply_markup
            resp = requests.post(url, json=fallback_payload, timeout=10)
            
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки: {e}")
        return False


def send_telegram_to_chat(text: str, chat_id: str, parse_mode: str = "HTML", reply_markup: dict = None) -> bool:
    """Отправка сообщения в конкретный чат (используется для event-driven). Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        logger.warning(f"[Notifier] TELEGRAM_BOT_TOKEN или chat_id ({chat_id}) не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
            
        resp = requests.post(url, json=payload, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            fallback_payload = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            }
            if reply_markup:
                fallback_payload["reply_markup"] = reply_markup
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
    try:
        new_corrs = get_new_correlations()
        if not new_corrs:
            return
            
        adapter = PolymarketAdapter()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("[Notifier] Нет API ключа для Арбитражника.")
            return
            
        arbitrage_agent = ArbitrageAgent(api_key=api_key, model="gemini-2.5-flash")
        
        for c in new_corrs[:5]:
            # Получаем свежие данные о рынках
            market_a = adapter.get_market(c['market1_id'])
            market_b = adapter.get_market(c['market2_id'])
            
            if not market_a or not market_b:
                continue
                
            signal = arbitrage_agent.analyze_correlation(
                market_a=market_a, 
                market_b=market_b, 
                correlation_type=c.get('correlation_type', 'thematic'),
                score=int(c.get('confidence', 0) * 100)
            )
            
            if signal and signal.has_arbitrage:
                alert_text = (
                    f"🚨 <b>НАЙДЕН КРОСС-РЫНОЧНЫЙ АРБИТРАЖ</b> 🚨\n\n"
                    f"📍 <b>Рынок A:</b> <a href='{market_a.url}'>{market_a.title}</a> (Цена: {market_a.price})\n"
                    f"📍 <b>Рынок B:</b> <a href='{market_b.url}'>{market_b.title}</a> (Цена: {market_b.price})\n\n"
                    f"💡 <b>Тип:</b> {signal.arbitrage_type}\n"
                    f"📈 <b>Разрыв (Spread):</b> {signal.spread_percent}%\n\n"
                    f"🧠 <b>Логика:</b> {signal.reasoning}\n\n"
                    f"⚡ <b>Трейд:</b> {signal.trade_instruction}\n"
                )
                notify(alert_text)
                
        # Отмечаем как прочитанные, даже если арбитража нет (чтобы не спамить)
        mark_correlations_notified([c['id'] for c in new_corrs[:5]])
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки корреляций: {e}")


# ─── Кросс-платформенный арбитраж (Polymarket ↔ Kalshi и др.) ──────────────

ARBITRAGE_TYPE_LABELS = {
    "price_divergence":      "💰 Прямое ценовое расхождение",
    "logical_contradiction": "🧠 Логическое противоречие",
    "pair_trade":            "🔗 Парный трейд",
}


def format_cross_arbitrage_alert(signal) -> str:
    """Форматирует CrossArbitrageSignal в красивое HTML-сообщение для Telegram."""
    emoji = "🔥" if signal.spread_percent >= 10 else "⚡️"
    type_label = ARBITRAGE_TYPE_LABELS.get(signal.arbitrage_type, signal.arbitrage_type)

    return (
        f"{emoji} <b>КРОСС-АРБИТРАЖ</b> | {type_label}\n\n"
        f"📊 Спред: <b>{signal.spread_percent:.1f}%</b> | "
        f"Match: {int(signal.match_score * 100)}%\n\n"
        f"<b>{signal.market_a_platform.upper()}</b>\n"
        f"<a href='{signal.market_a_url}'>{signal.market_a_title[:70]}</a>\n"
        f"Цена YES: <b>{int(signal.market_a_price * 100)}¢</b>\n\n"
        f"<b>{signal.market_b_platform.upper()}</b>\n"
        f"<a href='{signal.market_b_url}'>{signal.market_b_title[:70]}</a>\n"
        f"Цена YES: <b>{int(signal.market_b_price * 100)}¢</b>\n\n"
        f"💡 <b>Действие:</b>\n{signal.trade_instruction}\n\n"
        f"📝 <i>{signal.reasoning[:300]}</i>"
    )


def send_cross_arbitrage_alerts(min_spread: float = 5.0) -> None:
    """
    Отправляет в Telegram все новые кросс-арбитражные алерты из БД.
    Вызывать после run_cross_platform_scan().
    """
    try:
        from agents.shared.python.db import get_new_cross_arbitrage_signals, mark_cross_arbitrage_alerted
        from core.models import CrossArbitrageSignal

        new_signals = get_new_cross_arbitrage_signals(min_spread=min_spread)
        if not new_signals:
            return

        for row in new_signals:
            signal = CrossArbitrageSignal(**{k: row[k] for k in row if k != "id"})
            signal_id = row["id"]

            text = format_cross_arbitrage_alert(signal)
            success = send_telegram(text)

            if success:
                mark_cross_arbitrage_alerted(signal_id)
                logger.info(f"[Notifier] Кросс-арбитраж отправлен: {signal_id}")
            else:
                logger.warning(f"[Notifier] Не удалось отправить кросс-арбитраж: {signal_id}")
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки кросс-арбитража: {e}")
