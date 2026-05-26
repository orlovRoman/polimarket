# services/notifications.py
"""
Единый сервис Telegram-уведомлений.
Все отправки в Telegram идут только через этот модуль.
"""
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, logger


def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Базовая отправка сообщения. Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[Notifier] TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            }, timeout=10)
            
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки: {e}")
        return False


def send_telegram_to_chat(text: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    """Отправка сообщения в конкретный чат (используется для event-driven). Возвращает True при успехе."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        logger.warning(f"[Notifier] TELEGRAM_BOT_TOKEN или chat_id ({chat_id}) не задан.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
        
        # Если ошибка парсинга HTML, пробуем без форматирования
        if resp.status_code == 400 and parse_mode == "HTML":
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True
            }, timeout=10)
            
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки в чат {chat_id}: {e}")
        return False

def send_correlation_alerts(summary_callback=None) -> None:
    """Отправляет алерты о новых корреляциях. Перенесено из run_team.py."""
    from agents.shared.python.db import get_new_correlations, mark_correlations_notified
    notify = summary_callback or send_telegram
    try:
        new_corrs = get_new_correlations()
        if not new_corrs:
            return
        type_icons = {
            'causal': '🔄 ПРИЧИННАЯ', 'inverse': '↕️ ОБРАТНАЯ',
            'arbitrage': '⚡ АРБИТРАЖ', 'thematic': '🔗 ТЕМАТИЧЕСКАЯ'
        }
        alert_text = f"🔗 <b>Обнаружено {len(new_corrs)} корреляций между рынками:</b>\n\n"
        for i, c in enumerate(new_corrs[:5], 1):
            corr_type = type_icons.get(c.get('correlation_type', ''), c.get('correlation_type', ''))
            alert_text += (
                f"<b>{i}. {corr_type}</b> ({c.get('confidence', 0):.0%})\n"
                f"  📍 {c.get('title_a', c.get('market1_title', ''))}\n"
                f"  📍 {c.get('title_b', c.get('market2_title', ''))}\n"
                f"  → <i>{c.get('description', c.get('reasoning', ''))}</i>\n\n"
            )
        notify(alert_text)
        mark_correlations_notified([c['id'] for c in new_corrs[:5]])
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки корреляций: {e}")
