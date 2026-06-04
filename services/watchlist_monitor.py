"""
services/watchlist_monitor.py

Фоновый мониторинг watchlist-рынков.
Каждые 10 минут опрашивает цены всех рынков из списка 'watching'.
При изменении цены >= +50% за одно 10-минутное окно:
  - отправляет уведомление с последним сохранённым анализом
  - или запускает лёгкий анализ (только SCOUT) если анализа нет
  - дедуплицирует через sent_alerts
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("NexusPolyBot.WatchlistMonitor")

# Интервал опроса, секунды
POLL_INTERVAL_SEC = 10 * 60  # 10 минут
# Порог изменения цены (относительное изменение, от 0 до 1)
PRICE_CHANGE_THRESHOLD = 0.50
# Граничная цена, ниже которой рынок считается penny (дешевым)
MIN_BASE_PRICE = 0.05
# Лимит на размер вочлиста
MAX_WATCHLIST_SIZE = 50


async def run_watchlist_monitor(bot, chat_id: str) -> None:
    """
    Асинхронный фоновый цикл, проверяющий цены watchlist-рынков каждые 10 мин.
    Запускается как asyncio.Task из main.py.
    
    :param bot: Экземпляр aiogram Bot (для отправки уведомлений)
    :param chat_id: Telegram chat_id авторизованного пользователя
    """
    if not chat_id or chat_id.strip() == "":
        logger.warning("[WatchlistMonitor] TELEGRAM_CHAT_ID не задан. Фоновый мониторинг отключен.")
        return

    logger.info("[WatchlistMonitor] Фоновый мониторинг запущен.")
    await asyncio.sleep(30)  # немного ждём после старта, пока бот инициализируется
    
    while True:
        try:
            await _check_watchlist(bot, chat_id)
        except asyncio.CancelledError:
            logger.info("[WatchlistMonitor] Получен сигнал отмены, завершаем.")
            break
        except Exception as e:
            logger.error(f"[WatchlistMonitor] Ошибка в цикле мониторинга: {e}", exc_info=True)
        
        try:
            await asyncio.sleep(POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("[WatchlistMonitor] Ожидание прервано, завершаем.")
            break


async def _check_watchlist(bot, chat_id: str) -> None:
    """Проверяет watchlist один раз и отправляет уведомления при необходимости."""
    from agents.shared.python.db import (
        get_market_list, update_watchlist_price,
        is_alert_already_sent, mark_alert_sent
    )
    from agents.shared.adapters.polymarket import PolymarketAdapter
    
    entries = await asyncio.to_thread(get_market_list, 'watching')
    if not entries:
        return
        
    # Ограничиваем размер вочлиста
    if len(entries) > MAX_WATCHLIST_SIZE:
        logger.warning(f"[WatchlistMonitor] Размер watchlist ({len(entries)}) превышает лимит {MAX_WATCHLIST_SIZE}. Проверяются первые {MAX_WATCHLIST_SIZE} рынков.")
        entries = entries[:MAX_WATCHLIST_SIZE]
    
    logger.info(f"[WatchlistMonitor] Проверяю {len(entries)} рынков в watchlist...")
    adapter = PolymarketAdapter()
    sem = asyncio.Semaphore(5)
    
    # Временная метка цикла (почасовая точность для надежной дедупликации)
    cycle_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    
    async def process_entry(entry):
        async with sem:
            market_id = entry['market_id']
            base_price = entry.get('last_price') or entry.get('base_price')
            
            try:
                market = await asyncio.to_thread(adapter.get_market, market_id)
                if not market:
                    logger.warning(f"[WatchlistMonitor] Рынок {market_id} не найден в API.")
                    return
                
                current_price = market.price
                
                # Проверяем порог изменения цены
                if base_price is not None and base_price > 0:
                    is_penny = base_price < MIN_BASE_PRICE
                    abs_change = abs(current_price - base_price)
                    
                    trigger = False
                    change_pct = 0.0
                    if is_penny:
                        # Для дешевых рынков триггеримся только при абсолютном скачке цены >= 0.03 (3 цента)
                        if abs_change >= 0.03:
                            trigger = True
                            change_pct = abs_change / base_price if base_price > 0 else 0.0
                    else:
                        change_pct = abs_change / base_price
                        if change_pct >= PRICE_CHANGE_THRESHOLD:
                            trigger = True
                            
                    if trigger:
                        alert_key = f"watch_{market_id}_{cycle_ts}"
                        already_sent = await asyncio.to_thread(is_alert_already_sent, alert_key)
                        
                        if not already_sent:
                            logger.info(
                                f"[WatchlistMonitor] Триггер! {market_id}: "
                                f"{base_price:.3f} -> {current_price:.3f} (change: {change_pct * 100:.1f}%)"
                            )
                            await _send_watchlist_alert(
                                bot, chat_id, market, base_price, current_price, change_pct
                            )
                            await asyncio.to_thread(mark_alert_sent, alert_key, "watchlist_price_spike")
                
                # Обновляем last_price (это станет базой для следующего цикла)
                await asyncio.to_thread(update_watchlist_price, market_id, current_price)
                
            except Exception as e:
                logger.error(f"[WatchlistMonitor] Ошибка при проверке рынка {market_id}: {e}")

    # Запускаем задачи параллельно
    tasks = [process_entry(entry) for entry in entries]
    await asyncio.gather(*tasks)


async def _send_watchlist_alert(bot, chat_id: str, market, base_price: float, current_price: float, change_pct: float) -> None:
    """Формирует и отправляет уведомление о резком изменении цены."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.exceptions import TelegramAPIError
    
    direction = "📈" if current_price > base_price else "📉"
    change_str = f"{'+' if current_price > base_price else '-'}{abs(change_pct) * 100:.1f}%"
    price_yes_old = int(base_price * 100)
    price_yes_new = int(current_price * 100)
    
    # Получаем последний сохранённый анализ из signals
    analysis_text = await asyncio.to_thread(_get_last_analysis, market.id)
    
    header = (
        f"👁 <b>Watchlist: резкое изменение цены!</b>\n\n"
        f"<a href='{market.url}'>{market.title}</a>\n"
        f"{direction} YES: <b>{price_yes_old}¢ → {price_yes_new}¢</b> ({change_str})\n\n"
    )
    
    if analysis_text:
        body = f"📋 <b>Последний анализ агентов:</b>\n{analysis_text}"
    else:
        body = (
            "❗ <i>Сохранённого анализа нет.</i>\n"
            "Запустите /scan для анализа этого рынка."
        )
    
    text = header + body
    
    # Кнопки
    market_id_safe = market.id[:40]  # UUID = 36 символов, вписывается в лимит 64 байта
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🔙 Снять с наблюдения",
                callback_data=f"unlist_mkt_{market_id_safe}"
            )
        ]
    ])
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text[:4096],  # Telegram лимит
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    except TelegramAPIError as e:
        logger.error(f"[WatchlistMonitor] Не удалось отправить уведомление для {market.id}: {e}")


def _get_last_analysis(market_id: str) -> str:
    """
    Извлекает последний сохранённый анализ для рынка из таблицы signals.
    Возвращает краткий текст или пустую строку если анализа нет.
    """
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                """SELECT summary, details, type, edge, confidence, created_at
                   FROM signals WHERE market_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (market_id,)
            ).fetchone()
        
        if not row:
            return ""
        
        summary = row['summary'] or ""
        details = row['details'] or ""
        signal_type = row['type'] or ""
        edge = row['edge']
        confidence = row['confidence'] or 0
        created_at = row['created_at'] or ""
        
        # Форматируем краткий анализ
        text = ""
        if signal_type:
            text += f"Тип: <b>{signal_type}</b>\n"
        if edge is not None:
            text += f"Edge: <b>{edge * 100:.1f}%</b> | Уверенность: {confidence}\n"
        if summary:
            safe_summary = summary.replace('<', '&lt;').replace('>', '&gt;')
            text += f"📝 {safe_summary[:400]}\n"
        if details and details != summary:
            safe_details = details.replace('<', '&lt;').replace('>', '&gt;')
            text += f"<i>{safe_details[:200]}</i>\n"
        if created_at:
            text += f"<i>Анализ от: {created_at[:16]}</i>"
        
        return text.strip()
    except Exception as e:
        logger.error(f"[WatchlistMonitor] Ошибка при получении анализа для {market_id}: {e}")
        return ""
