from dataclasses import dataclass
from core.onchain_scorer import OnchainScore

@dataclass
class WhaleGateResult:
    allow: bool       # True = пропустить в SHADOW LLM
    reason: str       # объяснение для лога и Telegram

def check_whale_gate(oc_score: OnchainScore, min_confidence: float = 0.5) -> WhaleGateResult:
    """
    Если умные деньги уверенно против сигнала — блокируем LLM-вызов.
    Срабатывает только при наличии known_whales с историей (confidence > 0.5).
    """
    if oc_score.confidence < min_confidence:
        return WhaleGateResult(True, "Whale gate: данных недостаточно, пропускаем")

    if oc_score.direction == "CONTRA" and oc_score.whale_count >= 2:
        return WhaleGateResult(
            allow=False,
            reason=(
                f"🐋 Whale Gate: {oc_score.whale_count} known whale(s) торгуют "
                f"ПРОТИВ сигнала (score={oc_score.score:+.2f}, "
                f"conf={oc_score.confidence:.0%}). Авто-отклонение без LLM."
            )
        )
    return WhaleGateResult(True, "Whale gate: пропущен")
