def compute_sizing(
    ask_yes_lower: float,
    ask_no_upper: float,
    budget: float,
    max_single_leg_pct: float = 0.6,
) -> dict:
    """
    Расчет размеров ставок для синтетического коридора (равные контракты).
    Возвращает размеры и PnL в USD.
    """

    # Пытаемся купить равное количество контрактов, деля бюджет примерно пополам
    stake_per_leg = budget / 2
    stake_per_leg = min(stake_per_leg, budget * max_single_leg_pct)
    
    # Определяем кол-во контрактов по каждой ноге
    contracts_lower = stake_per_leg / ask_yes_lower
    contracts_upper = stake_per_leg / ask_no_upper
    
    # В идеале нужно строго одинаковое кол-во контрактов для безрискового арбитража.
    # Поэтому мы берем минимальное количество контрактов из двух ног и уравниваем их.
    target_contracts = min(contracts_lower, contracts_upper)
    
    # Пересчитываем реальные вложения под это количество контрактов
    invested_lower = target_contracts * ask_yes_lower
    invested_upper = target_contracts * ask_no_upper
    total_invested = invested_lower + invested_upper
    
    # Сценарий 1: выше upper — YES(lower) выигрывает, NO(upper) проигрывает
    pnl_above = target_contracts * 1.0 - total_invested
    
    # Сценарий 2: в коридоре — YES(lower) выигрывает, NO(upper) выигрывает
    pnl_corridor = target_contracts * 1.0 + target_contracts * 1.0 - total_invested
    
    # Сценарий 3: ниже lower — YES(lower) проигрывает, NO(upper) выигрывает
    pnl_below = target_contracts * 1.0 - total_invested
    
    min_pnl = min(pnl_above, pnl_below)
    
    return {
        "stake_lower_usd": round(invested_lower, 2),
        "stake_upper_usd": round(invested_upper, 2),
        "total_invested_usd": round(total_invested, 2),
        "contracts_lower": round(target_contracts, 2),
        "contracts_upper": round(target_contracts, 2),
        "pnl_above_upper_usd": round(pnl_above, 2),
        "pnl_in_corridor_usd": round(pnl_corridor, 2),
        "pnl_below_lower_usd": round(pnl_below, 2),
        "min_guaranteed_usd": round(min_pnl, 2),
        "max_win_usd": round(pnl_corridor, 2),
        "roi_min_pct": round(min_pnl / total_invested * 100, 2) if total_invested > 0 else 0,
        "roi_max_pct": round(pnl_corridor / total_invested * 100, 2) if total_invested > 0 else 0,
    }
