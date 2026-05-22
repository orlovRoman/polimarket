import os
import json
from datetime import datetime
from typing import Optional
from agents.shared.python.models import Market, AgentOpinion

class ShadowAgent:
    """
    Агент SHADOW — эксперт по анализу ликвидности, ордербука и объёмов.
    Его задача — верифицировать идеи SCOUT, основываясь на реальных данных
    ордербука (CLOB API) и истории цен.
    """
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        """
        Инициализация агента SHADOW.
        """
        self.api_key = api_key
        self.model = model
        
        # Загружаем детальные системные инструкции из файла конфигурации агента
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base_path, "GEMINI.md"), "r", encoding="utf-8") as f:
            self.system_instruction = f.read()

    def analyze_idea(self, market: Market, scout_opinion: str, orderbook: dict = None, price_history: list = None) -> Optional[AgentOpinion]:
        """
        Проводит анализ торговой идеи на предмет ликвидности и аномалий.
        
        :param market: Данные о рынке
        :param scout_opinion: Гипотеза от агента SCOUT
        :param orderbook: Данные ордербука от CLOB API (bid/ask depth, spread)
        :param price_history: История цен [{price, recorded_at}, ...]
        :return: Мнение агента (AgentOpinion) или None
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Форматируем данные ордербука
        orderbook_str = "Данные ордербука недоступны."
        if orderbook:
            orderbook_str = (
                f"=== ДАННЫЕ ОРДЕРБУКА (CLOB API) ===\n"
                f"Спред: {orderbook.get('spread', 'N/A')}\n"
                f"Top Bid: {orderbook.get('top_bid', 'N/A')} | Top Ask: {orderbook.get('top_ask', 'N/A')}\n"
                f"Глубина Bid (5 lvl): ${orderbook.get('bid_depth_5', 0):,.0f} | Ask: ${orderbook.get('ask_depth_5', 0):,.0f}\n"
                f"Всего уровней — Bids: {orderbook.get('total_bids', 0)} | Asks: {orderbook.get('total_asks', 0)}"
            )
            # Добавляем асимметрию
            bid_d = orderbook.get('bid_depth_5', 0)
            ask_d = orderbook.get('ask_depth_5', 0)
            if ask_d > 0:
                ratio = bid_d / ask_d
                orderbook_str += f"\nАсимметрия Bid/Ask: {ratio:.1f}x"
        
        # Форматируем историю цен
        price_history_str = "История цен недоступна."
        if price_history:
            lines = []
            for point in price_history[-6:]:  # Последние 6 точек
                lines.append(f"  {point['recorded_at']}: {point['price']:.4f}")
            if lines:
                price_history_str = "=== ИСТОРИЯ ЦЕНЫ ===\n" + "\n".join(lines)
                
        # Загружаем крупные сделки трейдеров (Smart Money) из БД
        from agents.shared.python.db import get_market_trader_transactions
        try:
            transactions = get_market_trader_transactions(market.id)
        except Exception as e:
            print(f"Ошибка получения транзакций трейдеров для {market.id}: {e}")
            transactions = []
            
        trader_transactions_str = "Данные о сделках крупных трейдеров отсутствуют."
        if transactions:
            tx_lines = []
            for tx in transactions[:10]:  # Берем последние 10 сделок
                alias_str = f" ({tx['alias']})" if tx.get('alias') else ""
                win_rate_str = f" | WinRate: {tx['win_rate'] * 100:.1f}%" if tx.get('win_rate') else ""
                price_str = f" по цене {tx['price']:.4f}" if tx.get('price') else ""
                tx_lines.append(
                    f"  - Кошелек: {tx['wallet_address']}{alias_str} | Ставка: {tx['outcome']} | "
                    f"Сумма: ${tx['amount_usd']:,.0f}{price_str}{win_rate_str} | Время: {tx['timestamp']}"
                )
            if tx_lines:
                trader_transactions_str = "=== КРУПНЫЕ СДЕЛКИ ТРЕЙДЕРОВ (SMART MONEY) ===\n" + "\n".join(tx_lines)
        
        # Загружаем RAG-память из Obsidian
        try:
            from agents.shared.utils.rag import get_rag_context
            rag_context = get_rag_context(market.title, market.description)
        except Exception as e:
            print(f"[SHADOW] Ошибка загрузки RAG-памяти: {e}")
            rag_context = "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"

        prompt = f"""
Сегодняшняя дата и время: {now_str}
Рынок: {market.title}
Текущая цена: {market.price}
Идея SCOUT: {scout_opinion}

{rag_context}

{orderbook_str}

{price_history_str}

{trader_transactions_str}

Проанализируй этот рынок на предмет рисков ликвидности, сделок крупных игроков и аномалий.
"""
        
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "generationConfig": {"response_mime_type": "application/json"}
        }
        
        from agents.shared.utils.gemini_client import generate_content_with_fallback
        result, active_model = generate_content_with_fallback(
            api_key=self.api_key,
            payload=payload,
            default_model=self.model,
            agent_name="SHADOW"
        )
        
        if not result:
            return None
            
        try:
            analysis = json.loads(result['candidates'][0]['content']['parts'][0]['text'])
            
            return AgentOpinion(
                agent_name="SHADOW",
                market_id=market.id,
                opinion=analysis.get("opinion", ""),
                confidence=float(analysis.get("confidence", 0.5)),
                agree=analysis.get("agree", False)
            )
        except Exception as e:
            print(f"Ошибка SHADOW при анализе {market.id}: {e}")
        return None


