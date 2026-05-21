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

async def trigger_nexus_scan(market_id: str, amount_usd: float):
    """
    Триггерит Orchestrator NEXUS для мгновенного точечного анализа рынка
    при обнаружении крупной сделки.
    """
    try:
        print(f"[Listener] 🚀 ТРИГГЕР: Крупная сделка (${amount_usd:,.0f})! Запуск внеочередного сканирования для {market_id}...")
        # Запускаем run_team.py в фоновом режиме для конкретного market_id
        # В нашем проекте run_team.py умеет запускаться с аргументом --market_id {id}
        # Проверим это позже, а пока сделаем вызов подпроцесса.
        import subprocess
        cmd = [sys.executable, str(PROJECT_ROOT / "run_team.py"), "--market_id", market_id]
        subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
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
    
    @client.on(events.NewMessage(chats='polymarketalerthub'))
    async def handler(event):
        text = event.message.message
        print(f"\n[Listener] 🔔 Получено новое сообщение из канала:\n{text[:120]}...")
        
        try:
            # Разбираем алерт
            bet_info = parse_whale_alert(text, event.message.entities)
            
            if not bet_info["wallet"]:
                print("[Listener] ⚠️ Не удалось извлечь адрес кошелька трейдера.")
                return
                
            if not bet_info["market_url"]:
                print("[Listener] ⚠️ Ссылка на рынок Polymarket не найдена.")
                return
                
            # Ищем ID рынков по ссылке
            market_ids = resolve_market_ids_from_url(bet_info["market_url"])
            if not market_ids:
                print(f"[Listener] ⚠️ Не удалось найти market_id для URL: {bet_info['market_url']}")
                return
                
            print(f"[Listener] Найдено {len(market_ids)} рынков для ссылки.")
            
            for m_id in market_ids:
                # Записываем в БД
                save_trader_transaction(
                    wallet_address=bet_info["wallet"],
                    market_id=m_id,
                    outcome=bet_info["outcome"] or "YES",
                    amount_usd=bet_info["amount_usd"],
                    price=bet_info["price"],
                    alias=bet_info["alias"]
                )
                print(f"[Listener] ✅ Сделка сохранена: Кошелек {bet_info['wallet']} | Сумма ${bet_info['amount_usd']:,.0f} | Исход {bet_info['outcome']} | Рынок {m_id}")
                
                # Триггерим мгновенный анализ, если сделка крупная (> $10,000 USD)
                if bet_info["amount_usd"] >= 10000.0:
                    await trigger_nexus_scan(m_id, bet_info["amount_usd"])
                    
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
