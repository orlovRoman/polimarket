import os
import re
import sys
import time
import asyncio
import threading
import httpx
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Подключаем корень проекта для правильных импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    TG_API_ID, TG_API_HASH, TG_PHONE, PROJECT_ROOT,
    GOOGLE_API_KEY, TELEGRAM_GROUP2_SOURCE,
    TELEGRAM_GROUP2_TARGET_ID, WHALE_ALERT_MIN_USD,
    TELEGRAM_BOT_ID
)
from agents.shared.python.db import save_trader_transaction, get_connection, save_telegram_post
from agents.shared.adapters.polymarket import PolymarketAdapter
from services.notifications import send_telegram as send_telegram_notify

from types import SimpleNamespace
from core.engine import CoreEngine
from agents.orchestrator.src.news_processor import NewsProcessor
from core.arb_scanner import _PRICE_TAG_RE

logger = logging.getLogger("NexusPolyBot.telegram_listener")

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
except ImportError:
    TelegramClient = None
    events = None
    FloodWaitError = Exception

_ENTITY_CACHE_TTL = 3600
_entity_username_cache: dict[int, tuple[str, float]] = {}
_cache_lock = threading.Lock()

def _get_cached_username(chat_id: int) -> str | None:
    with _cache_lock:
        entry = _entity_username_cache.get(chat_id)
        if entry and (time.time() - entry[1]) < _ENTITY_CACHE_TTL:
            return entry[0]
        return None

def _set_cached_username(chat_id: int, username: str) -> None:
    with _cache_lock:
        _entity_username_cache[chat_id] = (username, time.time())

def _is_target_source_match(chat_name: str, chat_id: int, target_sources: list[str]) -> bool:
    """Точное совпадение по username или chat_id, без подстрочного поиска."""
    name_lower = chat_name.lower()
    chat_id_str = str(chat_id)
    # Нормализуем: убираем -100 prefix для сравнения с пользовательским вводом
    clean_chat_id = chat_id_str.replace('-100', '').lstrip('-')
    
    for s in target_sources:
        clean_s = s.replace('@', '').lower()
        if (clean_s == name_lower or
            clean_s == chat_id_str or
            clean_s.lstrip('-') == clean_chat_id):
            return True
    return False

_API_ANALYZE_TIMEOUT = 10.0
_scan_in_progress = False

def _get_core_engine() -> CoreEngine:
    from core.singleton import get_core_engine as _get_shared_engine
    return _get_shared_engine()

# Папка для файла сессии Telethon
SESSION_DIR = PROJECT_ROOT / "vault"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH = str(SESSION_DIR / "userbot_session")

# Точные сигнатуры собственных сообщений бота.
# Не используем широкие паттерны вроде "Найдено" — они могут совпасть
# с легитимным контентом из новостных каналов.
_BOT_SIGNATURES = [
    "К сожалению, я не нашел связанных рынков",
    "Запущен внеочередной скан для рынка",
    "Анализирую...",
    "🗣️ Обсуждение рынка:",      # ← новое: итоговый отчёт консенсуса
    "🧠 SCOUT (Фундаментал):",   # ← новое: структура отчёта агентов
]

# Каналы, сообщения которых обрабатываются как whale/trader сигналы
_WHALE_CHANNELS = frozenset({"polymarketalerthub", "radarpolybot"})

def is_bot_message(text: str) -> bool:
    """
    Проверяет, является ли сообщение системным ответом самого бота.
    Служит защитой от бесконечного цикла, если бот и слушатель находятся в одной группе.
    Использует только точные строки-сигнатуры, чтобы не заглушить легитимные
    сообщения из русскоязычных новостных каналов.
    """
    return any(sig in text for sig in _BOT_SIGNATURES)

def restore_markdown_links(text: str, entities) -> str:
    """
    Восстанавливает скрытые markdown-ссылки из entities сообщения Telethon,
    превращая их в явный текстовый формат: 'Текст (URL)'.
    Идет с конца текста к началу, используя surrogate encoding для Telethon.
    """
    if not entities:
        return text
    try:
        from telethon.helpers import add_surrogate, del_surrogate
        original_text_s = add_surrogate(text)
        text_s = add_surrogate(text)
        seen_urls: set[str] = set()

        sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
        for ent in sorted_entities:
            if not (hasattr(ent, 'url') and ent.url):
                continue
            url = ent.url
            if url in seen_urls:
                continue
            offset = ent.offset
            length = ent.length
            
            anchor_text = del_surrogate(text_s[offset:offset+length])
            
            # 1. Если анкор сам по себе является ссылкой или ее частью
            clean_anchor = anchor_text.strip().lower()
            clean_url = url.replace("https://", "").replace("http://", "").replace("www.", "").strip().lower()
            if clean_url in clean_anchor:
                continue

            # 2. Если URL уже написан в тексте непосредственно рядом с анкором
            url_s = add_surrogate(url)
            window = len(url_s) + 10
            after_slice = original_text_s[offset+length:offset+length+window]
            before_slice = original_text_s[max(0, offset-window):offset]
            
            if url_s in after_slice or url_s in before_slice:
                continue
                
            replacement = add_surrogate(f"{anchor_text} ({url})")
            text_s = text_s[:offset] + replacement + text_s[offset+length:]
            seen_urls.add(url)

        return del_surrogate(text_s)
    except Exception as e:
        logger.error(f"[Listener] Ошибка восстановления ссылок из entities: {e}")
    return text

def _score_market(m, text: str) -> int:
    score = 0
    # Очищаем название рынка от ценового тега
    clean_title = _PRICE_TAG_RE.sub('', m.title).strip().lower()
    clean_text = text.lower()
    if clean_title in clean_text:
        score += 1000
        
    # Считаем пересечение слов ( len >= 2 для аббревиатур вроде ai, eu, us, fed )
    words_title = set(w for w in re.findall(r'[a-z0-9]+', clean_title) if len(w) >= 2)
    words_text = set(w for w in re.findall(r'[a-z0-9]+', clean_text) if len(w) >= 2)
    score += len(words_title.intersection(words_text)) * 10
    
    # Приоритет активным рынкам
    if 0.01 < m.price < 0.99:
        score += 5
    return score

def _parse_end_date(item: dict) -> datetime:
    for field in ("endDate", "end_date_iso", "endDateIso", "end"):
        raw = item.get(field)
        if raw:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, AttributeError):
                continue
    return datetime(2099, 12, 31, tzinfo=timezone.utc)

async def resolve_market_ids_from_url(url: str, text: str = "") -> list:
    """
    Вычленяет slug события или маркета из URL Polymarket, находит соответствующие рынки
    и сортирует их по релевантности к тексту сообщения text (асинхронно).
    """
    match = re.search(r'polymarket\.com/(?:event|market)/([a-zA-Z0-9_-]+)', url)
    if not match:
        return []
    slug = match.group(1)
    
    # Используем PolymarketAdapter для надежного получения рынков по slug (в отдельном потоке)
    adapter = PolymarketAdapter()
    try:
        markets = await asyncio.to_thread(adapter.get_event_by_slug, slug)
    except Exception as e:
        logger.error(f"[Resolver] Ошибка при запросе слага {slug} через адаптер: {e}")
        markets = []
        
    # Если адаптер не вернул рынки, делаем fallback на сырые асинхронные запросы
    if not markets:
        market_ids = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://gamma-api.polymarket.com/events", params={"slug": slug})
                if resp.status_code == 200:
                    event_data = resp.json()
                    if isinstance(event_data, list) and event_data:
                        event_id = event_data[0].get("id")
                        if event_id:
                            # Догружаем полную версию события по ID
                            resp_full = await client.get(f"https://gamma-api.polymarket.com/events/{event_id}")
                            if resp_full.status_code == 200:
                                full_event = resp_full.json()
                                for m in full_event.get("markets", []):
                                    if "id" in m:
                                        if m.get("closed") is True or m.get("closed") == "true":
                                            continue
                                        close_time = _parse_end_date(m)
                                        if close_time <= datetime.now(timezone.utc):
                                            continue
                                        market_ids.append(m["id"])
        except Exception as e:
            logger.error(f"[Resolver] Ошибка при fallback-запросе event-слага {slug}: {e}")
            
        if not market_ids:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get("https://gamma-api.polymarket.com/markets", params={"slug": slug})
                    if resp.status_code == 200:
                        markets_raw = resp.json()
                        if isinstance(markets_raw, list) and markets_raw:
                            for m in markets_raw:
                                if "id" in m:
                                    if m.get("closed") is True or m.get("closed") == "true":
                                        continue
                                    close_time = _parse_end_date(m)
                                    if close_time <= datetime.now(timezone.utc):
                                        continue
                                    market_ids.append(m["id"])
            except Exception as e:
                logger.error(f"[Resolver] Ошибка при fallback-запросе маркет-слага {slug}: {e}")
        return market_ids

    now = datetime.now(timezone.utc)

    def _is_market_active(m) -> bool:
        """Проверяет объект рынка (не dict) на активность."""
        # closed флаг
        closed_val = getattr(m, 'closed', False)
        if closed_val is True or closed_val == "true":
            return False

        # end date через атрибуты объекта
        for attr in ('end_date_iso', 'endDate', 'end'):
            raw = getattr(m, attr, None)
            if raw is not None:
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt > now
                except (ValueError, AttributeError):
                    continue

        # Проверяем close_time, если он есть
        close_time = getattr(m, 'close_time', None)
        if isinstance(close_time, datetime):
            dt = close_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > now
        return True  # если дата неизвестна — считаем активным

    if text:
        markets = sorted(markets, key=lambda m: _score_market(m, text), reverse=True)

    original_count = len(markets)
    markets = [m for m in markets if _is_market_active(m)]
    filtered_count = original_count - len(markets)

    if filtered_count > 0:
        logger.warning(
            f"[Resolver] ⏰ Отфильтровано {filtered_count} истёкших/закрытых рынков "
            f"для slug '{slug}'. Осталось активных: {len(markets)}"
        )
    if not markets:
        logger.warning(f"[Resolver] ⚠️ Все рынки для slug '{slug}' истекли или закрыты — пропускаем.")

    return [m.id for m in markets]

def parse_whale_alert(text: str, entities=None) -> dict:
    """
    Разбирает текст оповещения из канала polymarketalerthub.
    Ищет:
    - Ссылку на рынок Polymarket
    - Адрес кошелька (0x...) или ссылку на профиль
    - Направление сделки (YES/NO)
    - Объем сделки в USD
    - Цену покупки (опционально)
    """
    result = {
        "wallet": None,
        "alias": None,
        "market_url": None,
        "outcome": None,
        "amount_usd": 0.0,
        "price": None
    }
    
    # 1. Поиск ссылок на рынки Polymarket и профили трейдеров в entities сообщения
    urls = []
    if entities:
        for ent in entities:
            # Если это ссылка MessageEntityTextUrl
            if hasattr(ent, 'url') and ent.url:
                urls.append(ent.url)
            
    # Дополнительно ищем сырые ссылки в тексте
    raw_urls = re.findall(r'(https?://[^\s]+)', text)
    urls.extend(raw_urls)
    
    for url in urls:
        if "polymarket.com/event/" in url or "polymarket.com/market/" in url:
            result["market_url"] = url.split("?")[0] # убираем query-параметры
        elif "polymarket.com/profile/" in url:
            # Извлекаем кошелек из ссылки на профиль
            profile_match = re.search(r'polymarket\.com/profile/(0x[a-fA-F0-9]{40})', url)
            if profile_match:
                result["wallet"] = profile_match.group(1).lower()
            else:
                # Если в профиле юзернейм вместо кошелька
                username_match = re.search(r'polymarket\.com/profile/([a-zA-Z0-9_-]+)', url)
                if username_match:
                    result["alias"] = username_match.group(1)
                    # Используем имя как временный адрес кошелька, если реального нет
                    result["wallet"] = f"username:{result['alias'].lower()}"

    # 2. Ищем адрес кошелька прямо в тексте (если не нашли по ссылкам)
    if not result["wallet"]:
        wallet_match = re.search(r'(0x[a-fA-F0-9]{40})', text)
        if wallet_match:
            result["wallet"] = wallet_match.group(1).lower()
        else:
            # Ищем сокращенные кошельки вида 0x1234...abcd
            short_wallet_match = re.search(r'\b(0x[a-fA-F0-9]{4})\.\.\.([a-fA-F0-9]{4})\b', text)
            if short_wallet_match:
                # Создаем псевдо-кошелек для сохранения истории
                result["wallet"] = f"{short_wallet_match.group(1).lower()}...{short_wallet_match.group(2).lower()}"

    # 3. Ищем псевдоним (alias) в тексте
    # Часто пишется в скобках или после слова Trader: Trader "TrumpMegaBull" или (alias: TrumpMegaBull)
    alias_match = re.search(r'(?:alias|Trader|username):\s*\*?([a-zA-Z0-9_-]+)\*?', text, re.IGNORECASE)
    if alias_match and not result["alias"]:
        result["alias"] = alias_match.group(1)
    
    # 4. Ищем сумму сделки в USD (приоритет buy-контексту, затем fallback без P&L)
    bought_amount = re.search(
        r'\b(?:bought?|buy|buys?)\s+(?:YES|NO)\s+\$([0-9][0-9,]*(?:\.[0-9]+)?)',
        text, re.IGNORECASE
    )
    if bought_amount:
        result["amount_usd"] = float(bought_amount.group(1).replace(",", ""))
    else:
        # Убираем P&L контекст из поиска
        text_no_pnl = re.sub(r'P&L[^$]*\$[0-9,]+', '', text, flags=re.IGNORECASE)
        amount_match = re.search(r'\$([0-9][0-9,]*(?:\.[0-9]+)?)', text_no_pnl)
        if amount_match:
            result["amount_usd"] = float(amount_match.group(1).replace(",", ""))
        
    # 5. Ищем направление исхода (YES/NO) без слепого re.IGNORECASE для "no"
    if re.search(r'\b(?:bought?|buy|buy[s]?)\s+YES\b|\bYES\b(?=\s*\$|\s*@|\s*at\b)', text, re.IGNORECASE):
        result["outcome"] = "YES"
    elif re.search(r'\b(?:bought?|buy|buy[s]?)\s+NO\b|\bNO\b(?=\s*\$|\s*@|\s*at\b)', text, re.IGNORECASE):
        result["outcome"] = "NO"
    elif re.search(r'\bYES\b', text):
        result["outcome"] = "YES"
    elif re.search(r'\bNO\b', text):
        result["outcome"] = "NO"
        
    # 6. Ищем цену контракта (например, at 61.2¢ или @ 45¢ или price: 0.52)
    price_match = re.search(r'(?:at|@)\s*([0-9.]+)\s*¢', text, re.IGNORECASE)
    if price_match:
        result["price"] = float(price_match.group(1)) / 100.0
    else:
        # Вариант с долларами: at $0.45
        price_usd_match = re.search(r'(?:at|@)\s*\$([0-9.]+)', text)
        if price_usd_match:
            val = float(price_usd_match.group(1))
            if 0.0 < val <= 1.0:
                result["price"] = val
            
    return result

def parse_radar_signal(text: str, entities=None) -> dict:
    """
    Парсит DCA-сигнал трейдера из канала radarpolybot.

    Формат сообщения:
      [Market Title](https://polymarket.com/event/slug)
      ⚡️ Buy Yes
      ├ Amount: $11,136
      ├ Entry: 15¢ → Now: 90¢
      └ To win: $74,240 (6.7x)
      🧑💼 Trader: Parz1vaI · [Copy Trade](https://polymarket.com/profile/0x...)
      ├ Win Rate: 67%
      ├ P&L: +$5,187

    URL рынка и профиля трейдера приходят через Telegram entities (скрытые ссылки),
    а не как сырой текст — поэтому приоритет на entities, текст только как fallback.
    """
    result = {
        "wallet": None,
        "alias": None,
        "market_url": None,
        "outcome": None,
        "amount_usd": 0.0,
        "price": None,           # entry price (нормализованная)
        "entry_price": None,     # сырая entry цена в долях
        "current_price": None,   # текущая цена в долях
        "win_rate": None,        # int, процент
    }

    # 1. URL рынка и кошелька — приоритет из entities (скрытые ссылки Markdown)
    if entities:
        for ent in entities:
            if not (hasattr(ent, 'url') and ent.url):
                continue
            url = ent.url
            if "polymarket.com/event/" in url or "polymarket.com/market/" in url:
                if not result["market_url"]:
                    result["market_url"] = url.split("?")[0]
            elif "polymarket.com/profile/" in url:
                profile_match = re.search(r'/profile/(0x[a-fA-F0-9]{40})', url)
                if profile_match:
                    result["wallet"] = profile_match.group(1).lower()
                else:
                    username_match = re.search(r'/profile/([a-zA-Z0-9_-]+)', url)
                    if username_match:
                        result["alias"] = username_match.group(1)
                        result["wallet"] = f"username:{username_match.group(1).lower()}"

    # 2. Fallback: ищем raw URL в тексте (если entities нет или не дали результата)
    if not result["market_url"]:
        raw_urls = re.findall(r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', text)
        if raw_urls:
            result["market_url"] = raw_urls[0].split("?")[0]

    if not result["wallet"]:
        wallet_match = re.search(r'(0x[a-fA-F0-9]{40})', text)
        if wallet_match:
            result["wallet"] = wallet_match.group(1).lower()

    # 3. Outcome: "Buy Yes" / "Buy No"
    if re.search(r'\bBuy\s+Yes\b', text, re.IGNORECASE):
        result["outcome"] = "YES"
    elif re.search(r'\bBuy\s+No\b', text, re.IGNORECASE):
        result["outcome"] = "NO"
    elif re.search(r'\bYES\b', text):
        result["outcome"] = "YES"
    elif re.search(r'\bNO\b', text):
        result["outcome"] = "NO"

    # 4. Amount: $11,136
    amount_match = re.search(r'Amount:\s*\$([0-9,]+(?:\.[0-9]+)?)', text)
    if amount_match:
        result["amount_usd"] = float(amount_match.group(1).replace(",", ""))

    # 5. Entry: 15¢ → Now: 90¢
    entry_match = re.search(r'Entry:\s*([0-9.]+)\s*¢', text)
    if entry_match:
        entry_val = float(entry_match.group(1)) / 100.0
        result["entry_price"] = entry_val
        result["price"] = entry_val  # entry — это цена входа трейдера

    now_match = re.search(r'Now:\s*([0-9.]+)\s*¢', text)
    if now_match:
        result["current_price"] = float(now_match.group(1)) / 100.0

    # 6. Win Rate: 67%
    wr_match = re.search(r'Win Rate:\s*([0-9]+)%', text)
    if wr_match:
        result["win_rate"] = int(wr_match.group(1))

    # 7. Alias из строки "Trader: Name"
    alias_match = re.search(r'Trader:\s*([A-Za-z0-9_]+)', text)
    if alias_match and not result["alias"]:
        result["alias"] = alias_match.group(1)
        if not result["wallet"]:
            result["wallet"] = f"username:{alias_match.group(1).lower()}"

    return result

def build_tg_post_url(chat, msg_id: int) -> str:
    """
    Строит прямую ссылку на сообщение в Telegram.
    Публичный канал: t.me/{username}/{msg_id}
    Приватный канал: t.me/c/{clean_id}/{msg_id}
    """
    username = getattr(chat, 'username', None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    clean_id = str(chat.id).replace('-100', '')
    return f"https://t.me/c/{clean_id}/{msg_id}"

async def trigger_nexus_scan(market_id: str, amount_usd: float = 0.0, source: str = "whale", market_url: str = "", post_url: str = "", post_text: str = "", timeout_sec: float = 300.0):
    """
    Триггерит Orchestrator NEXUS для мгновенного точечного анализа рынка
    при обнаружении крупной сделки или новости.
    """
    try:
        if source == "whale":
            logger.info(f"[Listener] 🚀 ТРИГГЕР: Крупная сделка (${amount_usd:,.0f})! Запуск внеочередного сканирования для {market_id}...")
            msg_text = f"🚀 <b>ТРИГГЕР (Whale):</b> Запущен внеочередной скан для рынка <code>{market_id}</code>"
        else:
            logger.info(f"[Listener] 🗞 ТРИГГЕР: Важная новость ({source})! Запуск сканирования для {market_id}...")
            msg_text = f"🗞 <b>ТРИГГЕР (News):</b> Запущен внеочередной скан для рынка <code>{market_id}</code>"
            
        global _scan_in_progress
        if _scan_in_progress:
            logger.warning("[Listener] ⚠️ Скан уже выполняется, пропускаем новый триггер")
            return
            
        _scan_in_progress = True
        
        async def _run_scan():
            global _scan_in_progress
            try:
                eng = _get_core_engine()
                source_url = post_url or market_url or ""
                source_text = post_text or (
                    f"Whale transaction detected: ${amount_usd:,.0f}" if source == "whale"
                    else f"Triggered by: {source}"
                )
                await asyncio.wait_for(
                    asyncio.to_thread(
                        eng.run_team_discussion,
                        market_id=market_id,
                        trigger_type="event_driven",
                        source_url=source_url,
                        source_text=source_text
                    ),
                    timeout=timeout_sec
                )
            except asyncio.TimeoutError:
                logger.error(f"[Listener] ❌ trigger_nexus_scan: превышен таймаут {timeout_sec}с для {market_id}")
            except RuntimeError as e:
                logger.warning(f"[Listener] ⚠️ trigger_nexus_scan: сканирование занято: {e}")
            except Exception as e:
                logger.error(f"[Listener] ❌ trigger_nexus_scan: неожиданная ошибка: {e}\n{traceback.format_exc()}")
            finally:
                _scan_in_progress = False
                
        asyncio.create_task(_run_scan())
            
        # Отправляем подтверждение в Telegram
        send_telegram_notify(msg_text)
        
    except Exception as e:
        logger.error(f"[Listener] Ошибка запуска мгновенного сканирования: {e}\n{traceback.format_exc()}")

async def _should_ignore_message(event, target_chat_id) -> tuple[bool, Optional[object]]:
    """Возвращает (should_ignore, chat_or_None)."""
    chat = None
    try:
        chat = await event.get_chat()
        chat_id_str = str(getattr(chat, 'id', ''))
        if target_chat_id:
            clean_target = target_chat_id.replace('-100', '').lstrip('-')
            if clean_target in chat_id_str:
                return True, None

        text = event.message.message
        if not text:
            return True, None

        if TELEGRAM_BOT_ID:
            sender = await event.get_sender()
            if getattr(sender, 'id', None) == int(TELEGRAM_BOT_ID):
                return True, None
    except Exception:
        return False, None

    if is_bot_message(event.message.message):
        return True, None
    return False, chat

async def _resolve_chat_entity(chat, client):
    if not getattr(chat, 'username', None):
        cached_username = _get_cached_username(chat.id)
        if cached_username:
            return SimpleNamespace(
                username=cached_username,
                id=chat.id,
                title=getattr(chat, 'title', '')
            )
        else:
            try:
                full_entity = await client.get_entity(chat.id)
                uname = getattr(full_entity, 'username', None)
                if uname:
                    _set_cached_username(chat.id, uname)
                    return full_entity
            except FloodWaitError as e:
                logger.warning(f"[Listener] ⏳ FloodWait: get_entity заблокирован на {e.seconds}с для chat {chat.id}. Используем числовой ID.")
            except Exception as e:
                logger.warning(f"[Listener] ⚠️ Не удалось получить полный entity для {chat.id}: {e}")
    return chat

async def _process_target_source_analysis(chat, msg_id, text, tg_post_url, chat_name):
    post_id = await asyncio.to_thread(save_telegram_post, str(chat.id), msg_id, text)
    if post_id and TELEGRAM_GROUP2_TARGET_ID:
        logger.info(f"[Listener] 🧠 Триггерим глубокий анализ поста из {chat_name} (ID: {post_id})...")
        
        source_label = chat_name
        if text:
            first_line = text.split('\n')[0][:30]
            source_label = f"[{chat_name}] {first_line}..."
            
        async with httpx.AsyncClient(timeout=_API_ANALYZE_TIMEOUT) as c:
            try:
                await c.post(
                    f"http://127.0.0.1:8000/api/analyze/{post_id}",
                    json={
                        "post_id": post_id, 
                        "chat_id": str(TELEGRAM_GROUP2_TARGET_ID),
                        "source_chat_id": str(chat.id),
                        "source_username": getattr(chat, 'username', None),
                        "source_message_id": msg_id,
                        "source_url": tg_post_url,
                        "source_text": source_label
                    }
                )
            except httpx.TimeoutException:
                logger.warning(f"[Listener] ⏱️ Таймаут при вызове /api/analyze/{post_id} (>{_API_ANALYZE_TIMEOUT}с) — анализ не запущен")
            except Exception as e:
                logger.error(f"[Listener] Ошибка вызова API: {e}\n{traceback.format_exc()}")

async def _process_whale_channel_message(chat_name, text, entities, tg_post_url, is_target_source):
    if chat_name.lower() == "radarpolybot":
        bet_info = parse_radar_signal(text, entities)
        logger.info(f"[Listener] 🎯 radarpolybot сигнал: outcome={bet_info['outcome']} | "
                    f"amount=${bet_info['amount_usd']:,.0f} | entry={bet_info['entry_price']} | "
                    f"now={bet_info['current_price']} | market_url={bet_info['market_url']}")
    else:
        bet_info = parse_whale_alert(text, entities)

    if bet_info["wallet"] and bet_info["market_url"]:
        market_ids = await resolve_market_ids_from_url(bet_info["market_url"], text)
        if market_ids:
            for m_id in market_ids:
                await asyncio.to_thread(
                    save_trader_transaction,
                    wallet_address=bet_info["wallet"],
                    market_id=m_id,
                    outcome=bet_info["outcome"] or "YES",
                    amount_usd=bet_info["amount_usd"],
                    price=bet_info["price"],
                    alias=bet_info["alias"]
                )
                logger.info(f"[Listener] ✅ Сделка сохранена: Кошелек {bet_info['wallet']} | Сумма ${bet_info['amount_usd']:,.0f} | Исход {bet_info['outcome']} | Рынок {m_id}")

            if bet_info["amount_usd"] >= WHALE_ALERT_MIN_USD and not is_target_source:
                await trigger_nexus_scan(
                    market_ids[0],
                    amount_usd=bet_info["amount_usd"],
                    source="whale",
                    market_url=bet_info["market_url"] or "",
                    post_url=tg_post_url,
                    post_text=f"[{chat_name}] whale signal"
                )
    elif bet_info["market_url"] and not bet_info["wallet"]:
        market_ids = await resolve_market_ids_from_url(bet_info["market_url"], text)
        if market_ids and bet_info["amount_usd"] >= WHALE_ALERT_MIN_USD and not is_target_source:
            logger.warning("[Listener] ⚠️ Нет кошелька, но есть market_url. Только скан.")
            await trigger_nexus_scan(
                market_ids[0],
                amount_usd=bet_info["amount_usd"],
                source="whale",
                market_url=bet_info["market_url"],
                post_url=tg_post_url,
                post_text=f"[{chat_name}] whale signal"
            )

async def _process_news_channel_message(chat_name, text, entities, tg_post_url, news_processor, is_target_source):
    if not is_target_source:
        pm_url = None
        if entities:
            for ent in entities:
                if hasattr(ent, 'url') and ent.url:
                    if 'polymarket.com/event/' in ent.url or 'polymarket.com/market/' in ent.url:
                        pm_url = ent.url.split('?')[0]
                        break

        if not pm_url:
            pm_url_match = re.search(
                r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', text
            )
            if pm_url_match:
                pm_url = pm_url_match.group(0)

        if pm_url:
            market_ids = await resolve_market_ids_from_url(pm_url, text)
            if market_ids:
                logger.info(f"[Listener] 🔗 Найден прямой URL рынка в сигнале из {chat_name}. Триггерим.")
                await trigger_nexus_scan(
                    market_ids[0],
                    amount_usd=0.0,
                    source=chat_name,
                    market_url=pm_url,
                    post_url=tg_post_url,
                    post_text=f"[{chat_name}] news signal"
                )
            else:
                logger.info(f"[Listener] ⚪️ Пост был в группе {chat_name}, но рынок с URL {pm_url} не определен (пропускаем).")
            return

        markets = news_processor.find_relevant_markets(text)
        if markets:
            logger.info(f"[Listener] 🟢 Найдено {len(markets)} рынков для новости. Триггерим первый.")
            await trigger_nexus_scan(
                markets[0].id,
                amount_usd=0.0,
                source=chat_name,
                market_url=getattr(markets[0], 'url', ''),
                post_url=tg_post_url,
                post_text=f"[{chat_name}] news signal"
            )
        else:
            logger.info(f"[Listener] ⚪️ Пост был в группе {chat_name}, но соответствующий рынок не определен (пропускаем).")

async def handle_incoming_telegram_message(
    event,
    client,
    news_processor,
    target_sources,
    target_chat_id
):
    should_ignore, chat = await _should_ignore_message(event, target_chat_id)
    if should_ignore:
        return

    if chat is None:
        chat = await event.get_chat()
    text = event.message.message
    if event.message.entities:
        text = restore_markdown_links(text, event.message.entities)

    chat = await _resolve_chat_entity(chat, client)
    chat_name = chat.username or chat.title or str(chat.id)
    msg_id = event.message.id
    
    logger.info(f"\n[Listener] 🔔 Получено новое сообщение из {chat_name}:\n{text[:120]}...")
    tg_post_url = build_tg_post_url(chat, msg_id)
    logger.info(f"[Listener] 🔗 source_url = {tg_post_url}")
    
    try:
        is_target_source = _is_target_source_match(chat_name, chat.id, target_sources)
        if is_target_source:
            if "polymarketalerthub" in chat_name.lower():
                logger.warning("[Listener] ⚠️ ВНИМАНИЕ: polymarketalerthub добавлен в target_sources! Это приведет к двойному анализу (глубокий API + быстрый Nexus).")
            await _process_target_source_analysis(chat, msg_id, text, tg_post_url, chat_name)

        if chat_name.lower() in _WHALE_CHANNELS:
            await _process_whale_channel_message(chat_name, text, event.message.entities, tg_post_url, is_target_source)
        else:
            await _process_news_channel_message(chat_name, text, event.message.entities, tg_post_url, news_processor, is_target_source)
                
    except Exception as e:
        logger.error(f"[Listener] ❌ Ошибка при обработке сообщения: {e}\n{traceback.format_exc()}")

async def main():
    if TelegramClient is None:
        logger.error("[Listener] ❌ Telethon не установлен. Запустите: pip install telethon")
        return
        
    # Проверяем наличие учетных данных в .env
    api_id = TG_API_ID
    api_hash = TG_API_HASH
    phone = TG_PHONE
    
    if not api_id or not api_hash:
        logger.info("="*60)
        logger.info("ТАБЛИЦА НАСТНОЕК TELEGRAM USERBOT")
        logger.info("Для работы real-time слушателя вам нужны API ID и API Hash.")
        logger.info("Их можно получить за 2 минуты на сайте: https://my.telegram.org")
        logger.info("="*60)
        
        loop = asyncio.get_running_loop()
        api_id_input = (await loop.run_in_executor(None, input, "Введите ваш Telegram API ID: ")).strip()
        api_hash_input = (await loop.run_in_executor(None, input, "Введите ваш Telegram API Hash: ")).strip()
        phone_input = (await loop.run_in_executor(None, input, "Введите ваш номер телефона Telegram (в формате +79991234567): ")).strip()
        
        if not api_id_input or not api_hash_input:
            logger.error("Ошибка: Настройки не введены. Работа завершена.")
            return
            
        # Записываем в .env файл
        env_path = PROJECT_ROOT / ".env"
        lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                
        # ... (прочие строки остаются) ...
        lines = [l for l in lines if not any(x in l for x in ["TG_API_ID", "TG_API_HASH", "TG_PHONE"])]
        
        lines.append(f"\nTG_API_ID={api_id_input}\n")
        lines.append(f"TG_API_HASH={api_hash_input}\n")
        lines.append(f"TG_PHONE={phone_input}\n")
        
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        logger.info("[Listener] Настройки успешно сохранены в .env!")
        api_id = api_id_input
        api_hash = api_hash_input
        phone = phone_input
        
    logger.info(f"[Listener] Инициализация Telegram клиента (сессия: {SESSION_PATH})...")
    client = TelegramClient(SESSION_PATH, int(api_id), api_hash)
    
    # Инициализация NewsProcessor для новостных каналов
    news_processor = NewsProcessor(api_key=GOOGLE_API_KEY)
    
    chats_to_listen = list(_WHALE_CHANNELS)
    target_sources = []
    if TELEGRAM_GROUP2_SOURCE and TELEGRAM_GROUP2_SOURCE != "group2_source":
        target_sources = [s.strip().lower() for s in TELEGRAM_GROUP2_SOURCE.split(",")]
        for s in target_sources:
            if s not in chats_to_listen:
                chats_to_listen.append(s.replace('@', ''))
        
    TARGET_CHAT_ID = str(TELEGRAM_GROUP2_TARGET_ID) if TELEGRAM_GROUP2_TARGET_ID else None

    @client.on(events.NewMessage(chats=chats_to_listen))
    async def handler(event):
        await handle_incoming_telegram_message(
            event=event,
            client=client,
            news_processor=news_processor,
            target_sources=target_sources,
            target_chat_id=TARGET_CHAT_ID
        )

    # Запуск клиента Telegram
    logger.info(f"[Listener] Подключение к Telegram (авторизация по телефону: {phone})...")
    await client.start(phone=lambda: phone)
    logger.info("[Listener] 🎉 Успешно подключено! Слушатель канала @polymarketalerthub активен.")
    
    # Будем работать бесконечно
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Если запуск из обычной консоли
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[Listener] Работа слушателя остановлена.")
