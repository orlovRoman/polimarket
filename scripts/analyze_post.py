import sys
import os
import argparse
sys.path.append(os.getcwd())

from config import GOOGLE_API_KEY
from agents.shared.python.db import get_telegram_post_text, mark_telegram_post_status
from agents.orchestrator.src.news_processor import NewsProcessor
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.polymarket_swing_agent.src.agent import SwingAgent
from agents.polymarket_insider_agent.src.agent import ShadowAgent
from services.notifications import send_telegram_to_chat
from agents.shared.adapters.polymarket import PolymarketAdapter

def main(post_id: int, chat_id: str):
    text = get_telegram_post_text(post_id)
    if not text:
        print(f"Post {post_id} not found in DB.")
        return
    
    # 1. Ищем рынки
    np = NewsProcessor(api_key=GOOGLE_API_KEY)
    markets = np.find_relevant_markets(text)
    
    if not markets:
        send_telegram_to_chat("К сожалению, я не нашел связанных рынков на Polymarket для этого поста.", chat_id)
        mark_telegram_post_status(post_id, 'NO_MARKETS')
        return

    send_telegram_to_chat(f"Нашел {len(markets)} связанных рынков. Анализирую...", chat_id)
    
    adapter = PolymarketAdapter()
    scout = ScoutAgent(api_key=GOOGLE_API_KEY)
    swing = SwingAgent(api_key=GOOGLE_API_KEY)
    shadow = ShadowAgent(api_key=GOOGLE_API_KEY)
    
    for m in markets:
        try:
            full_m = adapter.get_market(m.id)
            if not full_m: continue
            
            # 2. Анализ с контекстом поста
            news_context = f"КОНТЕКСТ СООБЩЕНИЯ ИЗ TELEGRAM:\n{text}\n\n"
            
            signal = scout.analyze_idea(full_m, news_context)
            swing_signal = swing.analyze_idea(full_m, news_context)
            
            opinion_shadow = None
            if signal or swing_signal:
                active = swing_signal if swing_signal else signal
                orderbook = None
                if full_m.tokens:
                    try: orderbook = adapter.get_orderbook(full_m.tokens[0])
                    except: pass
                
                opinion_shadow = shadow.analyze_idea(full_m, active.details, orderbook=orderbook, price_history=[])
                
            # 3. Форматирование ответа
            summary_text = f"🗣 <b>Ответ на ваш пост (Рынок: {full_m.title}):</b>\n<a href='{full_m.url}'>{full_m.title}</a>\n\n"
            
            if signal:
                summary_text += f"🧠 <b>SCOUT:</b>\n🎯 Причина: {getattr(signal, 'signal_cause', 'N/A')}\n⚖️ Риск: {getattr(signal, 'signal_risk', 'N/A')}\n📝 Вердикт: {getattr(signal, 'signal_verdict', 'N/A')}\n\n"
            else:
                summary_text += f"🧠 <b>SCOUT:</b> ⚪️ Расхождение < MIN_EDGE\n\n"
                
            if swing_signal:
                summary_text += f"🏄‍♂️ <b>SWING:</b>\n"
                if getattr(swing_signal, 'recommendation', '') == 'buy':
                    summary_text += f"🔥 Катализатор: {getattr(swing_signal, 'catalyst', 'N/A')}\n"
                else:
                    summary_text += f"💤 Почему тихо: {getattr(swing_signal, 'catalyst_absence_reason', 'N/A')}\n"
                summary_text += f"⚖️ Риск: {getattr(swing_signal, 'swing_risk', 'N/A')}\n📝 Вердикт: {getattr(swing_signal, 'swing_verdict', 'N/A')}\n\n"
            else:
                summary_text += f"🏄‍♂️ <b>SWING:</b> ⚪️ Сигнал не сформирован\n\n"
                
            if opinion_shadow:
                status = "✅ СОГЛАСЕН" if opinion_shadow.agree else "❌ ПРОТИВ"
                liq_risk = getattr(opinion_shadow, 'liquidity_risk', 'medium').upper()
                summary_text += f"🛡 <b>SHADOW:</b> {status}\n💧 Ликвидность: {liq_risk}\n📊 Ордербук: {getattr(opinion_shadow, 'orderbook_facts', 'N/A')}\n⚖️ Исполнение: {getattr(opinion_shadow, 'risk_assessment', 'N/A')}\n📝 Вердикт: {getattr(opinion_shadow, 'shadow_verdict', 'N/A')}\n\n"
                
            shadow_ok = opinion_shadow and opinion_shadow.agree and getattr(opinion_shadow, 'liquidity_risk', 'medium') != "high"
            if (signal or swing_signal) and shadow_ok:
                summary_text += "✨ <b>ИТОГ: Консенсус достигнут! Отличная идея из поста.</b>"
            elif (signal or swing_signal):
                summary_text += "🛑 <b>ИТОГ: Идея есть, но SHADOW отклонил (риски исполнения).</b>"
            else:
                summary_text += "🛑 <b>ИТОГ: Из этого поста агенты не вытянули четкого сигнала.</b>"
                
            send_telegram_to_chat(summary_text, chat_id)
            
        except Exception as e:
            print(f"Error processing {m.id}: {e}")
            
    mark_telegram_post_status(post_id, 'COMPLETED')

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--post_id", type=int, required=True)
    parser.add_argument("--chat_id", type=str, required=True)
    args = parser.parse_args()
    main(args.post_id, args.chat_id)
