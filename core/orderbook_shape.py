from dataclasses import dataclass

@dataclass
class OrderbookShape:
    thin_ask_wall: bool      # слабое сопротивление (хорошо для SWING-UP)
    thin_bid_wall: bool      # слабая поддержка (хорошо для SWING-DOWN)
    ask_wall_price: float    # цена где ask wall начинается
    ask_wall_depth: float    # $USD глубины в ask wall зоне
    pumpability_score: float # 0.0–1.0, насколько легко двинуть цену
    annotation: str          # одна строка для промпта

def analyze_orderbook_shape(orderbook: dict, current_price: float) -> OrderbookShape:
    """
    Определяет 'пампабельность' рынка по стакану.
    Тонкий ask wall (< $200 за следующие 5 центов) = легко двинуть.
    """
    if not orderbook:
        return OrderbookShape(False, False, 0.0, 0.0, 0.0, "Ордербук недоступен")
    
    ask_depth = orderbook.get("ask_depth_5", 0) or 0
    bid_depth = orderbook.get("bid_depth_5", 0) or 0
    top_ask = orderbook.get("top_ask", current_price + 0.05) or current_price
    
    # Нормализуем значения, если они равны None
    if ask_depth is None:
        ask_depth = 0.0
    if bid_depth is None:
        bid_depth = 0.0
    if top_ask is None:
        top_ask = current_price + 0.05
        
    if ask_depth == 0 and bid_depth == 0:
        return OrderbookShape(False, False, 0.0, 0.0, 0.0, "Данные стакана отсутствуют")
        
    thin_ask = ask_depth < 200
    thin_bid = bid_depth < 200
    
    # Pumpability: чем тоньше ask, тем легче поднять
    pumpability = max(0.0, 1.0 - (ask_depth / 500.0)) if ask_depth > 0 else 0.8
    
    annotation = (
        f"Стакан: Ask ${ask_depth:.0f} / Bid ${bid_depth:.0f} "
        f"({'тонкий ask ✅' if thin_ask else 'глубокий ask ❌'} для памп-входа)"
    )
    return OrderbookShape(thin_ask, thin_bid, top_ask, ask_depth, pumpability, annotation)
