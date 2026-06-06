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
    new_corrs = []  # Баг #3: объявляем до try, чтобы finally мог обратиться к переменной
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

        arbitrage_agent = ArbitrageAgent(api_key=api_key, model="gemini-2.5-flash")

        for c in new_corrs[:5]:
            # Получаем свежие данные о рынках
            market_a = adapter.get_market(c['market_id_a'])
            market_b = adapter.get_market(c['market_id_b'])

            if not market_a or not market_b:
                continue

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
                elif spread is not None and spread >= 5.0:
                    header = f"⚠️ <b>ПОТЕНЦИАЛЬНАЯ ВОЗМОЖНОСТЬ ({platform_a} ↔ {platform_b})</b>"
                else:
                    logger.info(
                        f"[Notifier] Корреляция {c['id']}: пропущена (has_arbitrage=False, "
                        f"spread={spread:.1f}%, "
                        f"reason={getattr(signal,'reasoning','')[:80]})"
                    )
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
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки корреляций: {e}")
    finally:
        # Баг #3: помечаем как прочитанные даже при исключении — иначе следующий
        # запуск снова попытается обработать те же корреляции.
        if new_corrs:
            mark_correlations_notified([c['id'] for c in new_corrs[:5]])


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
    platform_a = signal.market_a_platform.upper()
    platform_b = signal.market_b_platform.upper()

    # ── Новый блок торговой рекомендации ───────────────────────────────────
    action_a = getattr(signal, "action_a", "SKIP")
    action_b = getattr(signal, "action_b", "SKIP")
    pnl      = getattr(signal, "expected_pnl_pct", None)
    risk     = getattr(signal, "risk_level", "MEDIUM")
    price_a  = getattr(signal, "entry_price_a_cents", None)
    price_b  = getattr(signal, "entry_price_b_cents", None)

    trade_block = ""
    if action_a != "SKIP" or action_b != "SKIP":
        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(risk, "🟡")
        pa_str = f" @ <b>{int(price_a)}¢</b>" if price_a is not None else ""
        pb_str = f" @ <b>{int(price_b)}¢</b>" if price_b is not None else ""
        if pnl is None:
            pnl_str = "N/A"
        elif pnl > 0:
            pnl_str = f"<b>+{pnl:.1f}%</b>"
        elif pnl < 0:
            pnl_str = f"<b>{pnl:.1f}%</b>"
        else:
            pnl_str = "<b>0.0%</b>"
        plat_a = signal.market_a_platform.upper()
        plat_b = signal.market_b_platform.upper()
        trade_block = (
            f"\n\n📋 <b>РЕКОМЕНДАЦИЯ:</b>\n"
            f"  {plat_a}: <b>{action_a}</b>{pa_str}\n"
            f"  {plat_b}: <b>{action_b}</b>{pb_str}\n"
            f"💰 Ожид. P&amp;L: {pnl_str} | Риск: {risk_emoji} {risk}"
        )
    # ────────────────────────────────────────────────────────────────────────

    return (
        f"{emoji} <b>КРОСС-АРБИТРАЖ ({platform_a} ↔ {platform_b})</b> | {type_label}\n\n"
        f"📊 Спред: <b>{signal.spread_percent:.1f}%</b> | "
        f"Match: {int(signal.match_score * 100)}%\n\n"
        f"<b>{platform_a}</b>\n"
        f"<a href='{signal.market_a_url}'>{signal.market_a_title[:70]}</a>\n"
        f"Цена YES: <b>{int(signal.market_a_price * 100)}¢</b>\n\n"
        f"<b>{platform_b}</b>\n"
        f"<a href='{signal.market_b_url}'>{signal.market_b_title[:70]}</a>\n"
        f"Цена YES: <b>{int(signal.market_b_price * 100)}¢</b>\n\n"
        f"💡 <b>Действие:</b>\n{signal.trade_instruction}\n\n"
        f"📝 <i>{signal.reasoning[:300]}</i>"
        f"{trade_block}"
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

        valid_fields = CrossArbitrageSignal.model_fields.keys()  # Баг #1: белый список полей модели
        for row in new_signals:
            signal = CrossArbitrageSignal.model_validate(  # Баг #1: игнорируем лишние колонки из БД
                {k: row[k] for k in row if k in valid_fields}
            )
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

def format_synthetic_corridor_alert(signal) -> str:
    return (
        f"🚨 <b>СИНТЕТИЧЕСКИЙ АРБИТРАЖ (Внутри Polymarket)</b> 🚨\n\n"
        f"<b>{signal.event_title}</b>\n"
        f"<a href='{signal.event_url}'>Перейти к событию</a>\n\n"
        f"📊 Нарушение монотонности: порог <b>${signal.upper_level}{signal.upper_level_unit}</b> стоит ДОРОЖЕ <b>${signal.lower_level}{signal.lower_level_unit}</b>!\n\n"
        f"🛒 <b>Стратегия исполнения:</b>\n"
        f"1. Покупаем YES на <i>{signal.lower_question}</i> по <b>{signal.lower_ask_yes:.3f}</b>\n"
        f"2. Покупаем NO на <i>{signal.upper_question}</i> по <b>{signal.upper_ask_no:.3f}</b>\n\n"
        f"💰 <b>Математика на бюджет ${signal.total_invested_usd:.0f}:</b>\n"
        f"Кол-во контрактов на каждую ногу: <b>{signal.contracts_lower:.1f} шт.</b>\n"
        f"Мин. гарантированный PnL: <b>${signal.min_guaranteed_usd:.2f}</b> (<b>+{signal.roi_min_pct:.1f}%</b>)\n"
        f"Макс. профит (попадание в коридор): <b>${signal.pnl_in_corridor_usd:.2f}</b> (<b>+{signal.roi_max_pct:.1f}%</b>)\n\n"
        f"📉 Глубина стакана (кол-во контрактов на лучших ценах): <b>{signal.executable_contracts:.0f}</b>"
    )

def send_synthetic_corridor_alerts() -> None:
    try:
        from agents.shared.python.db import get_unalerted_synthetic_corridors, mark_synthetic_corridor_alerted, is_alert_already_sent, mark_alert_sent
        from agents.polymarket_arbitrage_agent.src.synthetic.models import SyntheticCorridorSignal

        new_signals = get_unalerted_synthetic_corridors()
        if not new_signals:
            return

        for row in new_signals:
            valid_fields = SyntheticCorridorSignal.model_fields.keys()
            signal = SyntheticCorridorSignal.model_validate(
                {k: row[k] for k in row if k in valid_fields}
            )
            alert_key = signal.signal_id
            
            if is_alert_already_sent(alert_key):
                mark_synthetic_corridor_alerted(signal.signal_id)
                continue

            text = format_synthetic_corridor_alert(signal)
            success = send_telegram(text)

            if success:
                mark_alert_sent(alert_key, "synthetic_corridor")
                mark_synthetic_corridor_alerted(signal.signal_id)
                logger.info(f"[Notifier] Синтетический коридор отправлен: {signal.signal_id}")
            else:
                logger.warning(f"[Notifier] Не удалось отправить синтетический коридор: {signal.signal_id}")
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки синтетического коридора: {e}")

def format_temporal_corridor_alert(signal) -> str:
    p_corridor_str = f"P(коридор)=<b>{signal.p_in_corridor*100:.0f}%</b> | " if signal.p_in_corridor > 0 else ""
    
    def _format_dt(dt) -> str:
        from datetime import datetime
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError as e:
                logger.warning(f"[Notifier] Не удалось распарсить дату {dt!r}: {e}")
                return dt
        return dt.strftime('%d %b') if hasattr(dt, 'strftime') else str(dt)
        
    return (
        f"🕐 <b>Временной коридор (Temporal Arbitrage)</b>\n\n"
        f"📍 <b>{signal.event_title[:50]}</b>\n"
        f"📅 NO до <b>{_format_dt(signal.early_leg.expiry)}</b> ({signal.early_leg.entry_cost*100:.0f}¢)\n"
        f"📅 YES до <b>{_format_dt(signal.late_leg.expiry)}</b> ({signal.late_leg.entry_cost*100:.0f}¢)\n"
        # Баг #2: p_in_corridor не сохраняется в БД и всегда 0.0 — скрываем нулевое значение
        f"📊 {p_corridor_str}gap=<b>{signal.date_gap_days}д</b>\n"
        f"💰 Реальный спред: <b>+{signal.real_spread_pct:.1f}%</b> | Q-score: <b>{signal.quality_score:.2f}</b>\n"
        f"🎯 S1=${signal.pnl_s1_before_early:.0f} | S2=<b>${signal.pnl_s2_in_corridor:.0f}</b> | S3=${signal.pnl_s3_never:.0f}\n"
        f"💵 EV: <b>${signal.ev_usd:.2f}</b> (бюджет ${signal.early_stake_usd + signal.late_stake_usd:.0f})\n"
        f"🚪 {signal.exit_rule[:100]}\n"
        f"🔗 <a href='{signal.event_url}'>Открыть</a>"
    )

def send_temporal_corridor_alerts() -> None:
    try:
        from agents.shared.python.db import get_unalerted_temporal_corridors, mark_temporal_corridor_alerted, is_alert_already_sent, mark_alert_sent
        from datetime import datetime

        new_signals_data = get_unalerted_temporal_corridors()
        if not new_signals_data:
            return

        from dataclasses import dataclass

        @dataclass
        class TemporalLeg:
            expiry: datetime
            entry_cost: float

        @dataclass
        class TemporalSignalView:
            event_title: str
            event_url: str
            early_leg: TemporalLeg
            late_leg: TemporalLeg
            p_in_corridor: float
            date_gap_days: int
            real_spread_pct: float
            quality_score: float
            ev_usd: float
            early_stake_usd: float
            late_stake_usd: float
            exit_rule: str
            pnl_s1_before_early: float
            pnl_s2_in_corridor: float
            pnl_s3_never: float

        for row in new_signals_data:
            signal_id = row["signal_id"]
            
            # Cross-table deduplication using early__late format (which is signal_id)
            alert_key = signal_id
            if is_alert_already_sent(alert_key):
                mark_temporal_corridor_alerted(signal_id)
                continue

            total_stake = row.get("early_stake_usd", 0.0) + row.get("late_stake_usd", 0.0)
            
            sig = TemporalSignalView(
                event_title=row.get("event_title", ""),
                event_url=row.get("event_url", ""),
                early_leg=TemporalLeg(
                    expiry=datetime.fromisoformat(row["early_expiry"].replace("Z", "+00:00")),
                    entry_cost=row.get("early_cost", 0.0)
                ),
                late_leg=TemporalLeg(
                    expiry=datetime.fromisoformat(row["late_expiry"].replace("Z", "+00:00")),
                    entry_cost=row.get("late_cost", 0.0)
                ),
                p_in_corridor=0.0,
                date_gap_days=row.get("date_gap_days", 0),
                real_spread_pct=row.get("real_spread_pct", 0.0),
                quality_score=row.get("quality_score", 0.0),
                ev_usd=row.get("ev_usd", 0.0),
                early_stake_usd=row.get("early_stake_usd", 0.0),
                late_stake_usd=row.get("late_stake_usd", 0.0),
                exit_rule=row.get("exit_rule", ""),
                pnl_s1_before_early=row.get("late_contracts", 0.0) - total_stake,
                pnl_s2_in_corridor=row.get("early_contracts", 0.0) + row.get("late_contracts", 0.0) - total_stake,
                pnl_s3_never=row.get("early_contracts", 0.0) - total_stake
            )

            text = format_temporal_corridor_alert(sig)
            success = send_telegram(text)

            if success:
                mark_alert_sent(alert_key, "temporal_corridor")
                mark_temporal_corridor_alerted(signal_id)
                logger.info(f"[Notifier] Временной коридор отправлен: {signal_id}")
            else:
                logger.warning(f"[Notifier] Не удалось отправить временной коридор: {signal_id}")
    except Exception as e:
        logger.error(f"[Notifier] Ошибка отправки временного коридора: {e}")

async def send_compound_alert(bot, chat_id: int, opp) -> None:
    """Отправляет алерт о Favourite Compounding возможности с inline-кнопками."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    hours = opp.hours_left
    time_str = f"{hours:.1f}ч" if hours >= 1 else f"{hours*60:.0f}мин"
    price_cents = int(round(opp.price * 100))
    outcome = getattr(opp, "outcome", "YES")

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
        f"📌 Spread: {(opp.spread_pct or 0)*100:.2f}%"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"✅ Купить {outcome} ({price_cents}¢)",
            callback_data=f"compound_buy:{opp.opp_id}"
        ),
        InlineKeyboardButton(
            text="❌ Пропустить",
            callback_data=f"compound_skip:{opp.opp_id}"
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
    
    price_cents = int(round(current_price * 100))
    init_cents = int(round(opp["price"] * 100))
    outcome = opp.get("outcome", "YES")
    
    # Считаем ROI
    from services.favourite_compounder import ROICalculator
    from agents.shared.python.db import get_compound_settings
    cfg = get_compound_settings()
    virtual_stake = cfg.get("virtual_stake", 50.0)
    pnl = virtual_stake * (current_price - opp["price"]) / opp["price"] * (1.0 - ROICalculator.POLY_FEE_PCT)

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
            callback_data=f"compound_sell:{opp['id']}:{current_price}"
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
