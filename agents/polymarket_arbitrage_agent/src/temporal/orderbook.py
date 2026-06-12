import logging
from .loader import EventMarket

logger = logging.getLogger("NexusPolyBot.TemporalCorridor")

def fetch_real_entry_prices(
    early: EventMarket,
    late: EventMarket,
    session,
) -> dict | None:
    """
    Получает реальные цены входа через CLOB API.
    - NO(early): покупаем NO = продаём YES -> ask_no = 1 - best_bid_yes
    - YES(late): покупаем YES -> ask_yes = best_ask_yes
    """
    if not early.token_yes or not late.token_yes:
        logger.debug(
            f"[TC-ORDERBOOK] Отсутствует token_yes для early_market={early.market_id} (token={early.token_yes}) "
            f"или late_market={late.market_id} (token={late.token_yes})"
        )
        return None

    def get_book(token: str, label: str) -> dict | None:
        try:
            r = session.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token},
                timeout=8,
            )
            r.raise_for_status()
            book = r.json()
            if not book:
                logger.debug(f"[TC-ORDERBOOK] Получена пустая книга для {label} (token={token})")
            return book
        except Exception as e:
            logger.debug(f"[TC-ORDERBOOK] Ошибка запроса книги для {label} (token={token}): {e}")
            return None

    book_early = get_book(early.token_yes, f"early ({early.market_id})")
    book_late = get_book(late.token_yes, f"late ({late.market_id})")

    if not book_early or not book_late:
        logger.debug(
            f"[TC-ORDERBOOK] Одна из книг недоступна: early_book={bool(book_early)}, late_book={bool(book_late)}"
        )
        return None

    bids_early = book_early.get("bids", [])
    asks_late = book_late.get("asks", [])

    if not bids_early or not asks_late:
        logger.debug(
            f"[TC-ORDERBOOK] В книге отсутствуют нужные уровни. "
            f"early ({early.market_id}) bids={len(bids_early)}, "
            f"late ({late.market_id}) asks={len(asks_late)}"
        )
        return None

    # NO(early): продаём YES(early) по лучшему bid
    best_bid_yes_early = float(bids_early[0]["price"])
    ask_no_early = 1.0 - best_bid_yes_early
    ask_no_early_size = float(bids_early[0].get("size", 0))

    # YES(late): покупаем по лучшему ask
    best_ask_yes_late = float(asks_late[0]["price"])
    ask_yes_late_size = float(asks_late[0].get("size", 0))

    real_cost = ask_no_early + best_ask_yes_late
    real_spread_pct = (1.0 - real_cost) * 100

    executable = min(ask_no_early_size, ask_yes_late_size)

    depth_no = sum(float(b.get("size", 0)) for b in bids_early[:5])
    depth_yes = sum(float(a.get("size", 0)) for a in asks_late[:5])

    return {
        "ask_no_early": round(ask_no_early, 4),
        "ask_yes_late": round(best_ask_yes_late, 4),
        "real_cost": round(real_cost, 6),
        "real_spread_pct": round(real_spread_pct, 3),
        "executable_contracts": round(executable, 2),
        "depth_5_no_early": round(depth_no, 2),
        "depth_5_yes_late": round(depth_yes, 2),
    }

