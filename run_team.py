import os
import sys
from dotenv import load_dotenv

sys.path.append(os.getcwd())

from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.python.db import save_market, init_db, save_signal, get_last_analyzed_price, mark_market_analyzed
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent, save_opinion
from agents.polymarket_news_agent.src.agent import HeraldAgent

def run_team_discussion(log_callback=None, summary_callback=None, category=None):
    def log(msg):
        print(msg)
        if log_callback:
            log_callback(msg)

    load_dotenv()
    init_db()
    
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        log("GOOGLE_API_KEY не найден!")
        return

    adapter = PolymarketAdapter()
    scout = ScoutAgent(api_key=key)
    shadow = ShadowAgent(api_key=key)
    herald = HeraldAgent(api_key=key)

    cat_msg = f" в категории '{category}'" if category else ""
    log(f"--- 1. Поиск новых рынков{cat_msg} ---")
    markets = adapter.list_markets(limit=5, category=category)
    for m in markets:
        save_market(m)

    log(f"\\n--- 2. Обсуждение идей (SCOUT + SHADOW + HERALD) ---")
    new_markets_found = False
    for m in markets:
        last_price = get_last_analyzed_price(m.id)
        
        if last_price is not None:
            price_diff = abs(last_price - m.price)
            if price_diff < 0.03:
                log(f"\\n[РЫНОК]: {m.title} (Цена {m.price} почти не изменилась с {last_price}, пропускаем)")
                continue
            else:
                log(f"\\n[РЫНОК]: {m.title} (Цена изменилась: {last_price} -> {m.price}, АНАЛИЗИРУЕМ ЗАНОВО)")
        else:
            log(f"\\n[РЫНОК]: {m.title} (Новый рынок)")
            
        new_markets_found = True
        
        # 1. SCOUT дает оценку
        log("  SCOUT оценивает...")
        signal = scout.estimate_market(m)
        
        opinion_shadow = None
        opinion_herald = None

        if signal:
            log(f"  SCOUT: Нашел недооценку! Edge: {signal.edge:.2f}")
            
            # 2. SHADOW проверяет объемы/инсайды
            log("  SHADOW проверяет...")
            opinion_shadow = shadow.analyze_idea(m, signal.details)
            
            # 3. HERALD проверяет новости
            log("  HERALD проверяет...")
            opinion_herald = herald.analyze_idea(m, signal.details)
            
            # Собираем мнения
            for op in [opinion_shadow, opinion_herald]:
                if op:
                    save_opinion(op)
                    status = "✅ СОГЛАСЕН" if op.agree else "❌ НЕ СОГЛАСЕН"
                    log(f"  {op.agent_name}: {status} (Уверенность: {op.confidence})")
                    log(f"  Мнение {op.agent_name}: {op.opinion[:100]}...")

            # Консенсус: все должны быть согласны
            if opinion_shadow and opinion_herald and \
               opinion_shadow.agree and opinion_herald.agree and \
               opinion_shadow.confidence > 0.6 and opinion_herald.confidence > 0.6:
                
                log("  !!! ИДЕЯ ПОДТВЕРЖДЕНА ВСЕЙ КОМАНДОЙ. Сохраняем в сигналы.")
                save_signal(signal)
            else:
                log("  --- Консенсус не достигнут.")
        else:
            log("  SCOUT: Идея не найдена.")
            
        # Формируем и отправляем выжимку обсуждения
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