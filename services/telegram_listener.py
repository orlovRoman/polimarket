import os
import re
import sys
import time
import asyncio
import threading
import httpx
from pathlib import Path
from datetime import datetime
import requests

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

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
except ImportError:
    TelegramClient = None
    events = None
    FloodWaitError = Exception

_ENTITY_CACHE_TTL = 3600
_entity_username_cache: dict[int, tuple[str, float]] = {}

def _get_cached_username(chat_id: int) -> str | None:
    entry = _entity_username_cache.get(chat_id)
    if entry and (time.time() - entry[1]) < _ENTITY_CACHE_TTL:
        return entry[0]
    return None

def _set_cached_username(chat_id: int, username: str) -> None:
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

_core_engine_instance: CoreEngine | None = None
_core_engine_lock = threading.Lock()
_scan_semaphore = threading.Semaphore(1)

def _get_core_engine() -> CoreEngine:
    global _core_engine_instance
    with _core_engine_lock:
        if _core_engine_instance is None:
            _core_engine_instance = CoreEngine()
        return _core_engine_instance

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

def resolve_market_ids_from_url(url: str) -> list:
    """
    Вычленяет slug события или маркета из URL Polymarket и находит соответствующие market_id.
    """
    match = re.search(r'polymarket\.com/(?:event|market)/([a-zA-Z0-9_-]+)', url)
    if not match:
        return []
    slug = match.group(1)
    
    market_ids = []
    # 1. Пробуем получить event с этим слагом
    try:
        resp = requests.get("https://gamma-api.polymarket.com/events", params={"slug": slug}, timeout=10)
        if resp.status_code == 200:
            event_data = resp.json()
            if isinstance(event_data, list) and event_data:
                for m in event_data[0].get("markets", []):
                    if "id" in m:
                        market_ids.append(m["id"])
    except Exception as e:
        print(f"[Resolver] Ошибка при запросе event-слага {slug}: {e}")
        
    # 2. Если ничего не нашли, пробуем получить маркет с этим слагом
    if not market_ids:
        try:
            resp = requests.get("https://gamma-api.polymarket.com/markets", params={"slug": slug}, timeout=10)
            if resp.status_code == 200:
                markets = resp.json()
                if isinstance(markets, list) and markets:
                    for m in markets:
                        if "id" in m:
                            market_ids.append(m["id"])
        except Exception as e:
            print(f"[Resolver] Ошибка при запросе маркет-слага {slug}: {e}")
            
    return market_ids

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
    
    # 4. Ищем сумму сделки в USD (например, $15,000 или $1,250.50)
    amount_match = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)', text)
    if amount_match:
        result["amount_usd"] = float(amount_match.group(1).replace(",", ""))
        
    # 5. Ищем направление исхода (YES/NO)
    if re.search(r'\bYES\b', text, re.IGNORECASE):
        result["outcome"] = "YES"
    elif re.search(r'\bNO\b', text, re.IGNORECASE):
        result["outcome"] = "NO"
        
    # 6. Ищем цену контракта (например, at 61.2¢ или @ 45¢ или price: 0.52)
    price_match = re.search(r'(?:at|@)\s*([0-9.]+)\s*¢', text, re.IGNORECASE)
    if price_match:
        result["price"] = float(price_match.group(1)) / 100.0
    else:
        # Вариант с долларами: at $0.45
        price_usd_match = re.search(r'(?:at|@)\s*\$([0-9.]+)', text)
        if price_usd_match:
            result["price"] = float(price_usd_match.group(1))
            
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

async def trigger_nexus_scan(market_id: str, amount_usd: float = 0.0, source: str = "whale", market_url: str = "", post_url: str = "", post_text: str = ""):
    """
    Триггерит Orchestrator NEXUS для мгновенного точечного анализа рынка
    при обнаружении крупной сделки или новости.
    """
    try:
        if source == "whale":
            print(f"[Listener] 🚀 ТРИГГЕР: Крупная сделка (${amount_usd:,.0f})! Запуск внеочередного сканирования для {market_id}...")
            msg_text = f"🚀 <b>ТРИГГЕР (Whale):</b> Запущен внеочередной скан для рынка <code>{market_id}</code>"
        else:
            print(f"[Listener] 🗞 ТРИГГЕР: Важная новость ({source})! Запуск сканирования для {market_id}...")
            msg_text = f"🗞 <b>ТРИГГЕР (News):</b> Запущен внеочередной скан для рынка <code>{market_id}</code>"
            
        def _trigger_scan():
            if not _scan_semaphore.acquire(blocking=False):
                print("[Listener] ⚠️ Скан уже выполняется, пропускаем новый триггер")
                return
            try:
                eng = _get_core_engine()
                source_url = post_url or market_url or ""
                source_text = post_text or (
                    f"Whale transaction detected: ${amount_usd:,.0f}" if source == "whale"
                    else f"Triggered by: {source}"
                )
                eng.run_team_discussion(market_id=market_id, trigger_type="event_driven", source_url=source_url, source_text=source_text)
            except RuntimeError as e:
                print(f"[Listener] ⚠️ trigger_nexus_scan: сканирование занято: {e}")
            except Exception as e:
                print(f"[Listener] ❌ trigger_nexus_scan: неожиданная ошибка: {e}")
            finally:
                _scan_semaphore.release()
        threading.Thread(target=_trigger_scan, daemon=True).start()
            
        # Отправляем подтверждение в Telegram
        send_telegram_notify(msg_text)
        
    except Exception as e:
        print(f"[Listener] Ошибка запуска мгновенного сканирования: {e}")

async def main():
    if TelegramClient is None:
        print("[Listener] ❌ Telethon не установлен. Запустите: pip install telethon")
        return
        
    # Проверяем наличие учетных данных в .env
    api_id = TG_API_ID
    api_hash = TG_API_HASH
    phone = TG_PHONE
    
    if not api_id or not api_hash:
        print("="*60)
        print("ТАБЛИЦА НАСТРОЕК TELEGRAM USERBOT")
        print("Для работы real-time слушателя вам нужны API ID и API Hash.")
        print("Их можно получить за 2 минуты на сайте: https://my.telegram.org")
        print("="*60)
        
        api_id_input = input("Введите ваш Telegram API ID: ").strip()
        api_hash_input = input("Введите ваш Telegram API Hash: ").strip()
        phone_input = input("Введите ваш номер телефона Telegram (в формате +79991234567): ").strip()
        
        if not api_id_input or not api_hash_input:
            print("Ошибка: Настройки не введены. Работа завершена.")
            return
            
        # Записываем в .env файл
        env_path = PROJECT_ROOT / ".env"
        lines = []
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                
        # Удаляем старые записи, если они есть
        lines = [l for l in lines if not any(x in l for x in ["TG_API_ID", "TG_API_HASH", "TG_PHONE"])]
        
        lines.append(f"\nTG_API_ID={api_id_input}\n")
        lines.append(f"TG_API_HASH={api_hash_input}\n")
        lines.append(f"TG_PHONE={phone_input}\n")
        
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        print("[Listener] Настройки успешно сохранены в .env!")
        api_id = api_id_input
        api_hash = api_hash_input
        phone = phone_input
        
    print(f"[Listener] Инициализация Telegram клиента (сессия: {SESSION_PATH})...")
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
        
    @client.on(events.NewMessage(chats=chats_to_listen))
    async def handler(event):
        text = event.message.message
        if not text:
            return

        # Защита от бесконечного цикла:
        # Приоритет 1 — проверяем sender ID (надёжно, не зависит от текста)
        try:
            if TELEGRAM_BOT_ID:
                sender = await event.get_sender()
                if getattr(sender, 'id', None) == int(TELEGRAM_BOT_ID):
                    return
        except Exception:
            pass  # TELEGRAM_BOT_ID не задан — используем fallback

        # Приоритет 2 — точные строки-сигнатуры бота (fallback)
        if is_bot_message(text):
            return
            
        # Получаем имя канала
        chat = await event.get_chat()
        
        # Если username не получен — пробуем получить полный entity
        if not getattr(chat, 'username', None):
            cached_username = _get_cached_username(chat.id)
            if cached_username:
                chat = SimpleNamespace(
                    username=cached_username,
                    id=chat.id,
                    title=getattr(chat, 'title', '')
                )
            else:
                try:
                    full_entity = await client.get_entity(chat.id)
                    uname = getattr(full_entity, 'username', None)
                    if uname:
                        _set_cached_username(chat.id, uname)   # кэшируем только строку
                        chat = full_entity
                except FloodWaitError as e:
                    print(f"[Listener] ⏳ FloodWait: get_entity заблокирован на {e.seconds}с "
                          f"для chat {chat.id}. Используем числовой ID.")
                except Exception as e:
                    print(f"[Listener] ⚠️ Не удалось получить полный entity для {chat.id}: {e}")

        chat_name = chat.username or chat.title or str(chat.id)
        msg_id = event.message.id
        
        print(f"\n[Listener] 🔔 Получено новое сообщение из {chat_name}:\n{text[:120]}...")
        tg_post_url = build_tg_post_url(chat, msg_id)
        print(f"[Listener] 🔗 source_url = {tg_post_url}")
        
        try:
            is_target_source = False
            if _is_target_source_match(chat_name, chat.id, target_sources):
                is_target_source = True
                
                if "polymarketalerthub" in chat_name.lower():
                    print(f"[Listener] ⚠️ ВНИМАНИЕ: polymarketalerthub добавлен в target_sources! Это приведет к двойному анализу (глубокий API + быстрый Nexus).")
                    
                # 1. Запускаем глубокий Event-Driven анализ
                post_id = save_telegram_post(str(chat.id), msg_id, text)
                if post_id and TELEGRAM_GROUP2_TARGET_ID:
                    print(f"[Listener] 🧠 Триггерим глубокий анализ поста из {chat_name} (ID: {post_id})...")
                    
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
                            print(f"[Listener] ⏱️ Таймаут при вызове /api/analyze/{post_id} (>{_API_ANALYZE_TIMEOUT}с) — анализ не запущен")
                        except Exception as e:
                            print(f"[Listener] Ошибка вызова API: {e}")

            if chat_name.lower() in _WHALE_CHANNELS:
                # ── Ветка для whale/trader каналов ──────────────────────────────
                # Каждый канал имеет свой формат — выбираем нужный парсер
                if chat_name.lower() == "radarpolybot":
                    bet_info = parse_radar_signal(text, event.message.entities)
                    print(f"[Listener] 🎯 radarpolybot сигнал: outcome={bet_info['outcome']} | "
                          f"amount=${bet_info['amount_usd']:,.0f} | entry={bet_info['entry_price']} | "
                          f"now={bet_info['current_price']} | market_url={bet_info['market_url']}")
                else:
                    bet_info = parse_whale_alert(text, event.message.entities)
            
                if bet_info["wallet"] and bet_info["market_url"]:
                    market_ids = resolve_market_ids_from_url(bet_info["market_url"])
                    if market_ids:
                        for m_id in market_ids:
                            save_trader_transaction(
                                wallet_address=bet_info["wallet"],
                                market_id=m_id,
                                outcome=bet_info["outcome"] or "YES",
                                amount_usd=bet_info["amount_usd"],
                                price=bet_info["price"],
                                alias=bet_info["alias"]
                            )
                            print(f"[Listener] ✅ Сделка сохранена: "
                                  f"Кошелек {bet_info['wallet']} | "
                                  f"Сумма ${bet_info['amount_usd']:,.0f} | "
                                  f"Исход {bet_info['outcome']} | "
                                  f"Рынок {m_id}")
            
                        # Запускаем точечный скан если сумма достаточна и это не целевой глубокий анализ
                        if bet_info["amount_usd"] >= WHALE_ALERT_MIN_USD and not is_target_source:
                            await trigger_nexus_scan(
                                market_ids[0],
                                bet_info["amount_usd"],
                                source="whale",
                                market_url=bet_info["market_url"] or "",
                                post_url=tg_post_url,
                                post_text=text[:500]   # увеличили с 200 до 500 — whale-сигнал длиннее
                            )
                elif bet_info["market_url"] and not bet_info["wallet"]:
                    # Есть URL рынка но нет кошелька — минимальный триггер без сохранения транзакции
                    market_ids = resolve_market_ids_from_url(bet_info["market_url"])
                    if market_ids and bet_info["amount_usd"] >= WHALE_ALERT_MIN_USD and not is_target_source:
                        print(f"[Listener] ⚠️ Нет кошелька, но есть market_url. Только скан.")
                        await trigger_nexus_scan(
                            market_ids[0],
                            bet_info["amount_usd"],
                            source="whale",
                            market_url=bet_info["market_url"],
                            post_url=tg_post_url,
                            post_text=text[:500]
                        )
            
            else:
                # ── Ветка для новостных каналов ──────────────────────────────────
                if not is_target_source:
                    # Приоритет 1: URL рынка в entities (скрытые ссылки)
                    pm_url = None
                    if event.message.entities:
                        for ent in event.message.entities:
                            if hasattr(ent, 'url') and ent.url:
                                if 'polymarket.com/event/' in ent.url or 'polymarket.com/market/' in ent.url:
                                    pm_url = ent.url.split('?')[0]
                                    break
            
                    # Приоритет 2: сырой URL в тексте
                    if not pm_url:
                        pm_url_match = re.search(
                            r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', text
                        )
                        if pm_url_match:
                            pm_url = pm_url_match.group(0)
            
                    if pm_url:
                        market_ids = resolve_market_ids_from_url(pm_url)
                        if market_ids:
                            print(f"[Listener] 🔗 Найден прямой URL рынка в сигнале из {chat_name}. Триггерим.")
                            await trigger_nexus_scan(
                                market_ids[0],
                                source=chat_name,
                                market_url=pm_url,
                                post_url=tg_post_url,
                                post_text=text[:500]
                            )
                            return  # не тратим LLM-запрос
            
                    # Приоритет 3: LLM — только если URL не нашли
                    markets = news_processor.find_relevant_markets(text)
                    if markets:
                        print(f"[Listener] 🟢 Найдено {len(markets)} рынков для новости. Триггерим первый.")
                        await trigger_nexus_scan(
                            markets[0].id,
                            source=chat_name,
                            market_url=getattr(markets[0], 'url', ''),
                            post_url=tg_post_url,
                            post_text=text[:500]
                        )
                    else:
                        print(f"[Listener] ⚪️ Для новости из {chat_name} рынки на Polymarket не найдены.")
                    
        except Exception as e:
            print(f"[Listener] ❌ Ошибка при обработке сообщения: {e}")

    # Запуск клиента Telegram
    print(f"[Listener] Подключение к Telegram (авторизация по телефону: {phone})...")
    await client.start(phone=lambda: phone)
    print("[Listener] 🎉 Успешно подключено! Слушатель канала @polymarketalerthub активен.")
    
    # Будем работать бесконечно
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Если запуск из обычной консоли
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Listener] Работа слушателя остановлена.")
