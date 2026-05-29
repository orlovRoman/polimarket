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
    
    chats_to_listen = ['polymarketalerthub', 'radarpolybot']
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

            if "polymarketalerthub" in chat_name.lower():
                # 2. Сохраняем алерт о ките в БД в любом случае
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
                            print(f"[Listener] ✅ Сделка сохранена: Кошелек {bet_info['wallet']} | Сумма ${bet_info['amount_usd']:,.0f} | Исход {bet_info['outcome']} | Рынок {m_id}")
                        
                        # Если это НЕ целевой канал для глубокого анализа, запускаем старый точечный скан
                        if bet_info["amount_usd"] >= WHALE_ALERT_MIN_USD and market_ids and not is_target_source:
                            await trigger_nexus_scan(
                                market_ids[0], 
                                bet_info["amount_usd"], 
                                source="whale", 
                                market_url=bet_info["market_url"] or "",
                                post_url=tg_post_url,
                                post_text=text[:200]
                            )
            else:
                # 3. Ветка для других новостных групп (если они не попали в глубокий анализ)
                if not is_target_source:
                    # Сначала пробуем найти прямую ссылку на Polymarket в тексте
                    pm_url_match = re.search(
                        r'https?://polymarket\.com/(?:event|market)/[a-zA-Z0-9_-]+', text
                    )
                    if pm_url_match:
                        market_ids = resolve_market_ids_from_url(pm_url_match.group(0))
                        if market_ids:
                            print(f"[Listener] 🔗 Найден прямой URL рынка в сигнале из {chat_name}. Триггерим.")
                            await trigger_nexus_scan(
                                market_ids[0],
                                source=chat_name,
                                market_url=pm_url_match.group(0),
                                post_url=tg_post_url,
                                post_text=text[:200]
                            )
                            return   # не тратим LLM-запрос

                    # Только если прямой ссылки нет — используем LLM
                    markets = news_processor.find_relevant_markets(text)
                    if markets:
                        print(f"[Listener] 🟢 Найдено {len(markets)} рынков для новости. Триггерим первый.")
                        
                        # Передаём market_url чтобы source_url не деградировал до scheduled
                        await trigger_nexus_scan(
                            markets[0].id,
                            source=chat_name,
                            market_url=getattr(markets[0], 'url', ''),
                            post_url=tg_post_url,
                            post_text=text[:200]
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
