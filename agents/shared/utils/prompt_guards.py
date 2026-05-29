# agents/shared/utils/prompt_guards.py
"""
Утилиты для формирования блоков промпта.
Принцип: если данных нет — LLM получает явный запрет выдумывать,
а не пустую строку (которую он заполнит фантазией).
"""
from datetime import datetime, timezone
from typing import Optional


def guard_description(description: Optional[str], min_len: int = 20) -> str:
    """
    Оборачивает описание рынка в блок с явной меткой.
    Если description пустой или слишком короткий — возвращает предупреждение.
    """
    if description and len(description.strip()) > min_len:
        return (
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  ПРАВИЛА РАЗРЕШЕНИЯ РЫНКА (ОРАКУЛ) — ЧИТАТЬ ВНИМАТЕЛЬНО ║\n"
            "╚══════════════════════════════════════════════════════════╝\n"
            f"{description.strip()}\n"
            "══════════════════════════════════════════════════════════════\n"
            "ЗАДАЧА ПО ОРАКУЛУ: В поле oracle_risk ты ОБЯЗАН:\n"
            "1. Процитировать дословно ключевые критерии из текста выше\n"
            "   (кто источник, какая дата, какое числовое условие)\n"
            "2. Указать конкретные формулировки, которые допускают двоякое толкование\n"
            "3. НЕ писать общие фразы — только конкретику из правил выше\n"
        )
    return (
        "⚠️ ОПИСАНИЕ РЫНКА ОТСУТСТВУЕТ.\n"
        "В поле oracle_risk напиши строго: "
        "\"Описание рынка отсутствует — оракул-риск не определён, требуется ручная проверка.\"\n"
        "НЕ ПРИДУМЫВАЙ правила разрешения.\n"
    )


def guard_orderbook(orderbook: Optional[dict]) -> str:
    """
    Форматирует ордербук. Если данных нет — явный запрет выдумывать цифры.
    """
    if not orderbook:
        return (
            "⚠️ ОРДЕРБУК НЕДОСТУПЕН.\n"
            "В поле orderbook_facts напиши строго: "
            "\"Данные ордербука недоступны — оценка на основе price history.\"\n"
            "НЕ ПРИДУМЫВАЙ цифры спреда, глубины и уровней. "
            "Выставь confidence=0.30, liquidity_risk='medium'.\n"
        )
    bid_d = orderbook.get("bid_depth_5", 0)
    ask_d = orderbook.get("ask_depth_5", 0)
    ratio = (bid_d / ask_d) if ask_d > 0 else 0.0
    direction = "бычий сигнал" if ratio > 2 else ("медвежий сигнал" if ratio < 0.5 else "нейтрально")
    return (
        "=== ДАННЫЕ ОРДЕРБУКА (CLOB API) ===\n"
        f"Спред: {orderbook.get('spread', 'N/A')}\n"
        f"Top Bid: {orderbook.get('top_bid', 'N/A')} | Top Ask: {orderbook.get('top_ask', 'N/A')}\n"
        f"Глубина Bid (5 lvl): ${bid_d:,.0f} | Ask: ${ask_d:,.0f}\n"
        f"Асимметрия Bid/Ask: {ratio:.1f}x → {direction}\n"
        f"Всего уровней — Bids: {orderbook.get('total_bids', 0)} | Asks: {orderbook.get('total_asks', 0)}\n"
        "ИСПОЛЬЗУЙ эти числа в orderbook_facts — не перефразируй, цитируй.\n"
    )


def guard_smart_money(smart_money, target_outcome: str) -> str:
    """
    Форматирует сделки трейдеров. Если данных нет — явный запрет упоминать Smart Money.
    """
    if not smart_money or not getattr(smart_money, "available", False):
        return (
            "⚠️ ДАННЫХ О СДЕЛКАХ КРУПНЫХ ТРЕЙДЕРОВ НЕТ.\n"
            "В risk_assessment НЕ УПОМИНАЙ Smart Money как фактор — "
            "их отсутствие в данных не означает отсутствие в реальности.\n"
            "Пиши: \"Данные по крупным трейдерам недоступны.\"\n"
        )
    lines = [
        f"=== ОНЧЕЙН АКТИВНОСТЬ (Smart Money) ===",
        f"Всего объём YES: ${smart_money.total_yes_usd:,.0f}",
        f"Всего объём NO:  ${smart_money.total_no_usd:,.0f}",
        f"YES доминирование: {smart_money.yes_dominance:.0%}",
        "",
        "Топ трейдеры (по размеру ставки):",
    ]
    for tx in getattr(smart_money, "transactions", [])[:5]:
        alias = tx.get("alias") or tx.get("wallet", "unknown")[:10]
        wr = tx.get("win_rate")
        wr_label = f"win_rate={wr}%" if wr else "win_rate=неизвестен"
        lines.append(
            f"  • {alias}: {tx['outcome']} ${tx['amount_usd']:,.0f} | {wr_label}"
        )
    direction_label = target_outcome
    lines += [
        "",
        f"АНАЛИЗ: Сравни суммарный объём в сторону {direction_label} vs против.",
        "Трейдеры с win_rate ≥ 65% — сильное подтверждение. Указывай конкретные имена/адреса.",
        "НЕ пиши 'Smart Money подтверждают' без ссылки на конкретную строку выше.\n",
    ]
    return "\n".join(lines)


def guard_news_with_age(news_items: list, now: Optional[datetime] = None) -> str:
    """
    Форматирует новости с явной меткой возраста.
    Старые новости помечаются как не-катализаторы.
    """
    if not news_items:
        return (
            "⚠️ НОВОСТЕЙ НЕТ.\n"
            "catalyst невозможен — заполни catalyst_absence_reason с указанием "
            "что проверены: RSS, Reddit, Google Search — все молчат.\n"
        )
    now = now or datetime.now(timezone.utc)
    lines = ["=== НОВОСТИ (с меткой возраста) ==="]
    for item in news_items:
        pub = item.get("published_parsed") or item.get("published")
        title = item.get("title", "без заголовка")
        if pub:
            try:
                if isinstance(pub, (list, tuple)):
                    pub_dt = datetime(*pub[:6])
                else:
                    pub_dt = datetime.fromisoformat(str(pub))
                
                # Нормализация временных зон для предотвращения TypeError
                if now.tzinfo is not None and pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                elif now.tzinfo is None and pub_dt.tzinfo is not None:
                    pub_dt = pub_dt.replace(tzinfo=None)
                    
                age_h = (now - pub_dt).total_seconds() / 3600
                if age_h < 6:
                    tag = f"[🔥 {age_h:.0f}ч назад — СВЕЖИЙ КАТАЛИЗАТОР]"
                elif age_h < 24:
                    tag = f"[{age_h:.0f}ч назад]"
                elif age_h < 72:
                    tag = f"[{age_h/24:.0f}д назад — слабый катализатор]"
                else:
                    tag = "[СТАРАЯ НОВОСТЬ — НЕ КАТАЛИЗАТОР, не используй как импульс]"
            except Exception:
                tag = "[дата неизвестна — осторожно, проверь свежесть]"
        else:
            tag = "[дата неизвестна — осторожно]"
        lines.append(f"  {tag} {title}")
    return "\n".join(lines) + "\n"
