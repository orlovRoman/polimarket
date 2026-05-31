from dataclasses import dataclass


@dataclass
class HorizonStrategy:
    label: str            # "CRITICAL" | "SHORT" | "MEDIUM" | "LONG"
    instruction: str      # текст для промпта
    min_confidence: float # минимальный confidence для buy
    require_immediate_catalyst: bool


def get_horizon_strategy(hours_to_close: float) -> HorizonStrategy:
    if hours_to_close < 6:
        return HorizonStrategy(
            label="CRITICAL",
            instruction=(
                "⛔ ГОРИЗОНТ КРИТИЧЕСКИЙ (<6ч).\n"
                "ЕДИНСТВЕННОЕ условие buy: катализатор УЖЕ опубликован (новость из news_block, "
                "не прогноз). Без конкретной новости СЕГОДНЯ — строго IGNORE.\n"
                "target_exit_price обязан быть достижим за <6ч."
            ),
            min_confidence=0.72,
            require_immediate_catalyst=True,
        )
    elif hours_to_close < 24:
        return HorizonStrategy(
            label="SHORT",
            instruction=(
                "⚡ ГОРИЗОНТ КОРОТКИЙ (6–24ч).\n"
                "Катализатор должен произойти СЕГОДНЯ (объявление, матч, голосование, релиз). "
                "Укажи конкретное время события в catalyst."
            ),
            min_confidence=0.60,
            require_immediate_catalyst=True,
        )
    elif hours_to_close <= 72:
        return HorizonStrategy(
            label="MEDIUM",
            instruction=(
                "✅ ГОРИЗОНТ ОПТИМАЛЬНЫЙ (24–72ч).\n"
                "Ищи ожидаемые события (конференции, дедлайны, публикации). "
                "Оцени вероятность их наступления и влияния на цену."
            ),
            min_confidence=0.52,
            require_immediate_catalyst=False,
        )
    else:
        return HorizonStrategy(
            label="LONG",
            instruction=(
                "📅 ГОРИЗОНТ ДЛИННЫЙ (>72ч).\n"
                "Вход только при экстремальном hype_score (>0.75) или структурной неэффективности рынка. "
                "Долгосрочные позиции подвержены event-risk — учти это в swing_risk."
            ),
            min_confidence=0.65,
            require_immediate_catalyst=False,
        )
