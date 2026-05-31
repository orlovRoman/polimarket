def swing_decision(
    hype_score: float,
    price: float,
    llm_confidence: float = 0.5,
    llm_direction: str = "YES",
    use_llm_blend: bool = False
) -> tuple[str, float]:
    """
    Returns (recommendation, confidence) blending LLM inputs or using formula logic.
    """
    effective_price = price if llm_direction == "YES" else (1.0 - price)
    is_cheap = effective_price < 0.20

    if use_llm_blend:  # вызов из агента с LLM-данными
        final = 0.35 * hype_score + 0.65 * llm_confidence
        if final >= 0.52 and is_cheap:
            return "buy", round(final, 3)
        return "ignore", round(max(0.1, 1.0 - final), 3)

    # Старая логика (обратная совместимость)
    cheap_yes = price < 0.15
    cheap_no  = (1.0 - price) < 0.15
    is_old_cheap  = cheap_yes or cheap_no

    if hype_score >= 0.70 and is_old_cheap:
        return "buy", min(0.5 + hype_score * 0.3, 0.85)
    elif hype_score >= 0.55 and is_old_cheap:
        return "buy", 0.45
    return "ignore", max(0.1, 1.0 - hype_score)


