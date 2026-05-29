def swing_decision(hype_score: float, price: float) -> tuple[str, float]:
    """
    Returns (recommendation, confidence) без LLM.
    hype_score: float 0..1 из calculate_hype_potential
    price: текущая рыночная цена YES
    """
    cheap_yes = price < 0.15
    cheap_no  = (1.0 - price) < 0.15
    is_cheap  = cheap_yes or cheap_no

    if hype_score >= 0.70 and is_cheap:
        return "buy", min(0.5 + hype_score * 0.3, 0.85)
    elif hype_score >= 0.55 and is_cheap:
        return "buy", 0.45
    else:
        return "ignore", max(0.1, 1.0 - hype_score)
