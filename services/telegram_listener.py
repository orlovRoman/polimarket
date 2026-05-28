import os
import re
import sys
import asyncio
from pathlib import Path
from datetime import datetime
import requests

# Подключаем корень проекта для правильных импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import TG_API_ID, TG_API_HASH, TG_PHONE, PROJECT_ROOT
from agents.shared.python.db import save_trader_transaction, get_connection
from agents.shared.adapters.polymarket import PolymarketAdapter

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
            events = resp.json()
            if isinstance(events, list) and events:
                for m in events[0].get("markets", []):
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

async def trigger_nexus_scan(market_id: str, amount_usd: float = 0.0, source: str = "whale", market_url: str = ""):
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
            
        # Запускаем внеочередное сканирование через API движка
        import threading
        from core.engine import CoreEngine
        def _trigger_scan():
            eng = CoreEngine()
            source_url = market_url or ""
            source_text = ""
            if source == "whale" and amount_usd:
                source_text = f"Whale transaction detected: ${amount_usd:,.0f}"
            else:
                source_text = f"Triggered by: {source}"
            eng.run_team_discussion(market_id=market_id, trigger_type="event_driven", source_url=source_url, source_text=source_text)
        threading.Thread(target=_trigger_scan, daemon=True).start()
            
        # Отправляем подтверждение в Telegram
        from services.notifications import send_telegram
        send_telegram(msg_text)
        
    except Exception as e:
        print(f"[Listener] Ошибка запуска мгновенного сканирования: {e}")

async def main():
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
        
    from telethon import TelegramClient, events
    
    print(f"[Listener] Инициализация Telegram клиента (сессия: {SESSION_PATH})...")
    client = TelegramClient(SESSION_PATH, int(api_id), api_hash)
    
    # Инициализация NewsProcessor для новостных каналов
    from config import GOOGLE_API_KEY, TELEGRAM_GROUP2_SOURCE, TELEGRAM_GROUP2_TARGET_ID, WHALE_ALERT_MIN_USD
    from agents.orchestrator.src.news_processor import NewsProcessor
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
            from config import TELEGRAM_BOT_ID
            if TELEGRAM_BOT_ID:
                sender = await event.get_sender()
                if getattr(sender, 'id', None) == int(TELEGRAM_BOT_ID):
                    return
        except (ImportError, Exception):
            pass  # TELEGRAM_BOT_ID не задан — используем fallback

        # Приоритет 2 — точные строки-сигнатуры бота (fallback)
        if is_bot_message(text):
            return
            
        # Получаем имя канала
        chat = await event.get_chat()
        chat_name = chat.username or chat.title or str(chat.id)
        
        print(f"\n[Listener] 🔔 Получено новое сообщение из {chat_name}:\n{text[:120]}...")
        
        try:
            is_target_source = False
            if any(s in chat_name.lower() or s.replace('@', '') in chat_name.lower() or s == str(chat.id) for s in target_sources):
                is_target_source = True
                # 1. Запускаем глубокий Event-Driven анализ
                from agents.shared.python.db import save_telegram_post
                post_id = save_telegram_post(str(chat.id), event.message.id, text)
                if post_id and TELEGRAM_GROUP2_TARGET_ID:
                    print(f"[Listener] 🧠 Триггерим глубокий анализ поста из {chat_name} (ID: {post_id})...")
                    import httpx
                    import asyncio
                    async def trigger_analysis():
                        username = getattr(chat, 'username', None)
                        msg_id = event.message.id
                        if username:
                            source_url = f"https://t.me/{username}/{msg_id}"
                        else:
                            clean_id = str(chat.id).replace('-100', '')
                            source_url = f"https://t.me/c/{clean_id}/{msg_id}"
                        
                        # Короткий заголовок для отображения (source_label)
                        source_label = chat_name
                        if text:
                            first_line = text.split('\n')[0][:30]
                            source_label = f"[{chat_name}] {first_line}..."
                            
                        async with httpx.AsyncClient() as c:
                            try:
                                await c.post(
                                    f"http://127.0.0.1:8000/api/analyze/{post_id}",
                                    json={
                                        "post_id": post_id, 
                                        "chat_id": str(TELEGRAM_GROUP2_TARGET_ID),
                                        "source_chat_id": str(chat.id),
                                        "source_username": username,
                                        "source_message_id": msg_id,
                                        "source_url": source_url,
                                        "source_text": source_label
                                    }
                                )
                            except Exception as e:
                                print(f"[Listener] Ошибка вызова API: {e}")
                    asyncio.create_task(trigger_analysis())

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
                            await trigger_nexus_scan(market_ids[0], bet_info["amount_usd"], source="whale", market_url=bet_info["market_url"] or "")
            else:
                # 3. Ветка для других новостных групп (если они не попали в глубокий анализ)
                if not is_target_source:
                    markets = news_processor.find_relevant_markets(text)
                    if markets:
                        print(f"[Listener] 🟢 Найдено {len(markets)} рынков для новости. Триггерим первый.")
                        # Передаём market_url чтобы source_url не деградировал до scheduled
                        await trigger_nexus_scan(
                            markets[0].id,
                            source=chat_name,
                            market_url=getattr(markets[0], 'url', '')
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
