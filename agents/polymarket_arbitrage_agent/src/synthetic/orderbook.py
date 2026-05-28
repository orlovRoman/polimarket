from .event_loader import OutcomeMarket

def fetch_real_entry_prices(
    lower: OutcomeMarket,
    upper: OutcomeMarket,
    session,
) -> dict | None:
    """
    Получает реальные цены входа через CLOB API.
    Для конструкции нам нужны:
      - ask YES(lower): лучшая цена покупки YES нижнего уровня
      - ask NO(upper):  лучшая цена покупки NO верхнего уровня (= 1 - best_bid_yes_upper)
    """
    if not lower.token_yes or not upper.token_yes:
        return None
    
    def get_book(token_id: str) -> dict | None:
        try:
            resp = session.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id},
                timeout=8,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None
    
    book_lower_yes = get_book(lower.token_yes)
    book_upper_yes = get_book(upper.token_yes)
    
    if not book_lower_yes or not book_upper_yes:
        return None
    
    asks_lower = book_lower_yes.get("asks", [])
    bids_upper = book_upper_yes.get("bids", [])
    
    if not asks_lower or not bids_upper:
        return None
    
    ask_yes_lower = float(asks_lower[0]["price"])
    ask_yes_lower_size = float(asks_lower[0].get("size", 0))
    
    best_bid_yes_upper = float(bids_upper[0]["price"])
    ask_no_upper = 1.0 - best_bid_yes_upper
    ask_no_upper_size = float(bids_upper[0].get("size", 0))
    
    real_cost = ask_yes_lower + ask_no_upper
    real_spread_pct = (1.0 - real_cost) * 100
    
    executable_size = min(ask_yes_lower_size, ask_no_upper_size)
    
    depth_lower = sum(float(a.get("size", 0)) for a in asks_lower[:5])
    depth_upper = sum(float(b.get("size", 0)) for b in bids_upper[:5])
    
    return {
        "ask_yes_lower": round(ask_yes_lower, 4),
        "ask_no_upper": round(ask_no_upper, 4),
        "real_cost": round(real_cost, 6),
        "real_spread_pct": round(real_spread_pct, 3),
        "executable_size_contracts": round(executable_size, 2),
        "depth_5_lower": round(depth_lower, 2),
        "depth_5_upper": round(depth_upper, 2),
        "ask_levels_lower": len(asks_lower),
        "bid_levels_upper": len(bids_upper),
    }
