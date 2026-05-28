def compute_sizing(
    ask_no_early: float,
    ask_yes_late: float,
    p_before: float,
    p_in_corridor: float,
    p_never: float,
    budget: float = 200.0,
) -> dict:
    """
    Sizing для NO(early) + YES(late) в равных контрактах.
    Вкладываем равный бюджет в обе ноги.
    """
    real_cost = ask_no_early + ask_yes_late
    real_spread_pct = (1.0 - real_cost) * 100

    # Количество контрактов каждой ноги (равные)
    # Точнее: тратим budget/2 на каждую ногу
    contracts_no = (budget / 2.0) / ask_no_early
    contracts_yes = (budget / 2.0) / ask_yes_late

    # Берём минимум для исполнения в равных контрактах
    n = min(contracts_no, contracts_yes)

    stake_no = round(n * ask_no_early, 2)
    stake_yes = round(n * ask_yes_late, 2)
    total = stake_no + stake_yes

    pnl_s1 = round(n * (1.0 - real_cost), 2)   # spread
    pnl_s2 = round(n * (2.0 - real_cost), 2)   # двойная выплата
    pnl_s3 = round(n * (1.0 - real_cost), 2)   # spread (симметрично S1)

    # EV = взвешенное среднее по implied probabilities
    ev = round(p_before * pnl_s1 + p_in_corridor * pnl_s2 + p_never * pnl_s3, 2)

    return {
        "early_stake_usd": stake_no,
        "late_stake_usd": stake_yes,
        "early_contracts": round(n, 2),
        "late_contracts": round(n, 2),
        "total_invested": round(total, 2),
        "pnl_s1_before_early": pnl_s1,
        "pnl_s2_in_corridor": pnl_s2,
        "pnl_s3_never": pnl_s3,
        "min_pnl": pnl_s1,
        "max_pnl": pnl_s2,
        "ev_usd": ev,
        "roi_min_pct": round(pnl_s1 / total * 100, 2) if total > 0 else 0.0,
        "roi_max_pct": round(pnl_s2 / total * 100, 2) if total > 0 else 0.0,
        "real_spread_pct": round(real_spread_pct, 3),
    }


def compute_exit_rule(
    early_expiry,
    late_expiry,
    p_never: float,
) -> str:
    """Детерминированное правило выхода — без LLM."""
    days_total = (late_expiry - early_expiry).days
    close_days_before = max(7, days_total // 10)

    if p_never > 0.5:
        urgency = "ВЫСОКИЙ риск — рынок считает событие маловероятным"
    elif p_never > 0.3:
        urgency = "СРЕДНИЙ риск"
    else:
        urgency = "НИЗКИЙ риск"

    return (
        f"Закрыть YES({late_expiry.strftime('%d %b')}) принудительно если: "
        f"(1) цена упадёт ниже {(1-p_never)*0.7:.2f} (−30% от текущей) "
        f"ИЛИ (2) до экспирации < {close_days_before} дней. "
        f"Текущий риск незакрытия: {urgency}."
    )
