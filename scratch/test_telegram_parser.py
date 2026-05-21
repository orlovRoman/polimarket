import sys
from pathlib import Path
import io

# Принудительно устанавливаем UTF-8 для корректного вывода Unicode на Windows
if sys.platform.startswith('win'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Подключаем корень проекта
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.telegram_listener import parse_whale_alert
from agents.shared.python.db import init_db, save_trader_transaction, get_market_trader_transactions

def test_parser():
    print("=== ТЕСТИРОВАНИЕ РЕГУЛЯРНЫХ ВЫРАЖЕНИЙ ПАРСЕРА ===")
    
    # Тестовые сообщения разного формата
    messages = [
        # Формат 1: Ссылка на профиль в тексте + полная ссылка на рынок
        """
        🐳 Whale Trader 0x5a2d614a8909875f812a81234908ef1234a81234 (alias: TrumpMegaBull) bet $45,000 on YES at 61.2¢
        Market: Will Donald Trump win the 2024 Presidential Election?
        Link: https://polymarket.com/event/will-donald-trump-win-the-2024-presidential-election
        """,
        # Формат 2: Юзернейм профиля в URL + сокращенный кошелек + YES + доллары в цене
        """
        Trader 0x3f5c...a09d (username: CryptoKing) placed $120,500 on NO at $0.45 on market "Will Bitcoin hit $100k?"
        Check profile: https://polymarket.com/profile/CryptoKing
        Market: https://polymarket.com/market/will-bitcoin-hit-100k
        """,
        # Формат 3: Сырое сообщение без явного алиаса
        """
        Whale bet: $8,200 on YES @ 35¢ | https://polymarket.com/event/will-ethereum-gas-drop
        Trader: 0x81b01234567890abcdef1234567890abcdef1234
        """
    ]
    
    for i, msg in enumerate(messages, 1):
        print(f"\n--- Тест {i} ---")
        parsed = parse_whale_alert(msg)
        print(f"Текст:\n{msg.strip()}")
        print("-" * 20)
        print(f"Кошелек:  {parsed['wallet']}")
        print(f"Псевдоним: {parsed['alias']}")
        print(f"Сумма:    ${parsed['amount_usd']:,.2f}")
        print(f"Исход:    {parsed['outcome']}")
        print(f"Цена:     {parsed['price']}")
        print(f"Рынок:    {parsed['market_url']}")

def test_db_integration():
    print("\n=== ИНТЕГРАЦИОННЫЙ ТЕСТ БАЗЫ ДАННЫХ ===")
    # Инициализируем БД
    init_db()
    
    test_market_id = "test-market-123"
    test_wallet = "0x9999999999999999999999999999999999999999"
    
    print(f"1. Добавляем mock-транзакцию для кошелька {test_wallet}...")
    save_trader_transaction(
        wallet_address=test_wallet,
        market_id=test_market_id,
        outcome="YES",
        amount_usd=55000.0,
        price=0.62,
        alias="PolymarketGod"
    )
    
    # Установим win_rate для этого кошелька
    from agents.shared.python.db import update_wallet_stats
    update_wallet_stats(test_wallet, win_rate=0.72, total_profit=150000.0)
    print("   Установлен WinRate 72.0% для кошелька.")
    
    print("\n2. Извлекаем транзакции по рынку из БД...")
    txs = get_market_trader_transactions(test_market_id)
    for tx in txs:
        print(f"   [БД] Найдена сделка: {tx['alias']} ({tx['wallet_address']}) | "
              f"Сумма: ${tx['amount_usd']:,.0f} | Исход: {tx['outcome']} | WinRate: {tx['win_rate']*100:.1f}%")
        
    assert len(txs) > 0, "Транзакция не найдена в БД!"
    print("✅ Тест интеграции с базой данных успешно пройден!")

if __name__ == "__main__":
    test_parser()
    test_db_integration()
