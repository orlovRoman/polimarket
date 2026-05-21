import os
import sys
from dotenv import load_dotenv

# Добавляем корень проекта в путь поиска модулей
sys.path.append(os.getcwd())

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, init_db, save_signal, get_last_analyzed_price, mark_market_analyzed, cleanup_stale_signals
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent, save_opinion
from agents.polymarket_news_agent.src.agent import HeraldAgent
from agents.shared.utils.database import DatabaseManager
from agents.shared.python.market_selector import MarketSelector

def run_team_discussion(log_callback=None, summary_callback=None, category=None):
    """
    Координирует обсуждение рынков командой AI-агентов.
    """
    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    # Загружаем настройки и инициализируем базу данных
    load_dotenv()
    init_db()
    
    # Получаем лимит сканирования из БД (Layer 1 memory)
    db = DatabaseManager()
    scan_limit = int(db.get_memory("scan_limit") or 10)  # Дефолт: 10 рынков за цикл
    log(f"Параметры сессии: Лимит запросов (рынков) = {scan_limit}")

    # Очищаем устаревшие сигналы перед новым сканом
    stale = cleanup_stale_signals()
    if stale > 0:
        log(f"Очищено устаревших сигналов: {stale}")

    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        log("Критическая ошибка: GOOGLE_API_KEY не найден в .env!")
        return

    # Инициализируем адаптер платформы и агентов
    adapter = PolymarketAdapter()
    scout = ScoutAgent(api_key=key)
    shadow = ShadowAgent(api_key=key)
    herald = HeraldAgent(api_key=key)

    cat_msg = f" в категории '{category}'" if category else " (авто-микс)"
    log(f"--- 1. Поиск новых рынков{cat_msg} ---")
    
    # Умный отбор рынков через MarketSelector
    selector = MarketSelector(adapter)
    markets = selector.select(total_limit=scan_limit, category=category)
    
    if not category:
        auto_cat = selector.get_auto_category()
        log(f"  Категория ротации: {auto_cat}")
    
    log(f"  Отобрано рынков после фильтрации: {len(markets)}")

    for m in markets:
        save_market(m) # Сохраняем/обновляем данные о рынке в БД

    log(f"\n--- 2. Обсуждение идей (SCOUT + SHADOW + HERALD) ---")
    log(f"Всего рынков для проверки: {len(markets)}")
    
    new_markets_found = False
    for m in markets:
        # Проверяем, анализировали ли мы этот рынок ранее при такой же цене
        last_price = get_last_analyzed_price(m.id)
        
        if last_price is not None:
            price_diff = abs(last_price - m.price)
            # Если цена изменилась незначительно (менее 3%), пропускаем повторный анализ
            if price_diff < 0.03:
                log(f"\n[РЫНОК]: {m.title} (Цена {m.price} стабильна, пропускаем)")
                continue
            else:
                log(f"\n[РЫНОК]: {m.title} (Цена изменилась: {last_price} -> {m.price}, пересматриваем)")
        else:
            log(f"\n[РЫНОК]: {m.title} (Новый рынок в системе)")
            
        new_markets_found = True
        
        # ЭТАП 1: SCOUT ищет математическую недооценку (Edge)
        log("  SCOUT оценивает...")
        signal = scout.estimate_market(m)
        
        opinion_shadow = None
        opinion_herald = None

        if signal:
            log(f"  SCOUT: Нашел недооценку! Ожидаемый Edge: {signal.edge:.2f}")
            
            # ЭТАП 2: SHADOW анализирует объемы торгов и активность крупных кошельков
            log("  SHADOW проверяет...")
            opinion_shadow = shadow.analyze_idea(m, signal.details)
            
            # ЭТАП 3: HERALD ищет новости и проверяет, не завершилось ли событие досрочно
            log("  HERALD проверяет...")
            opinion_herald = herald.analyze_idea(m, signal.details)
            
            # Сохраняем мнения всех агентов в базу данных для истории
            for op in [opinion_shadow, opinion_herald]:
                if op:
                    save_opinion(op)
                    status = "✅ СОГЛАСЕН" if op.agree else "❌ НЕ СОГЛАСЕН"
                    log(f"  {op.agent_name}: {status} (Уверенность: {op.confidence})")
                    log(f"  Мнение {op.agent_name}: {op.opinion[:100]}...")

            # ЛОГИКА КОНСЕНСУСА:
            # Идея принимается только если оба эксперта (Shadow и Herald) согласны
            # и их уверенность в своем решении выше порога (0.6)
            if opinion_shadow and opinion_herald and \
               opinion_shadow.agree and opinion_herald.agree and \
               opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
                
                log("  !!! ИДЕЯ ПОДТВЕРЖДЕНА КОНСЕНСУСОМ. Генерируем сигнал.")
                save_signal(signal)
            else:
                log("  --- Консенсус не достигнут. Идея отклонена экспертами.")
        else:
            log("  SCOUT: Математическое преимущество не обнаружено.")
            
        # Формируем и отправляем краткое резюме для Telegram-интерфейса
        if summary_callback:
            summary_text = f"🗣 <b>Обсуждение рынка:</b>\\n<a href='{m.url}'>{m.title}</a>\\n\\n"
            if signal:
                summary_text += f"<b>SCOUT</b> 🟢 Нашел потенциал (Edge: {signal.edge:.2f})\\n\\n"
            else:
                summary_text += f"<b>SCOUT</b> ⚪️ Идея не найдена.\\n\\n"
            
            if opinion_shadow:
                status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
                summary_text += f"<b>SHADOW</b> {status} (Увер: {opinion_shadow.confidence})\\n<i>{opinion_shadow.opinion}</i>\\n\\n"
            
            if opinion_herald:
                status = "✅ СОГЛАСЕН" if opinion_herald.agree else "❌ ПРОТИВ"
                summary_text += f"<b>HERALD</b> {status} (Увер: {opinion_herald.confidence})\\n<i>{opinion_herald.opinion}</i>\\n\\n"
            
            if signal and opinion_shadow and opinion_herald and \
               opinion_shadow.agree and opinion_herald.agree and \
               opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
                summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Идея сохранена.</b>"
            elif signal:
                summary_text += "🛑 <b>ИТОГ: Консенсус не достигнут.</b>"
            else:
                summary_text += "🛑 <b>ИТОГ: Нет предмета для обсуждения.</b>"
                
            summary_callback(summary_text)
            
        # Отмечаем рынок как проанализированный с текущей ценой
        mark_market_analyzed(m.id, m.price)

    if not new_markets_found:
        log("\nНет рынков для обсуждения (цены не изменились).")

if __name__ == "__main__":
    run_team_discussion()