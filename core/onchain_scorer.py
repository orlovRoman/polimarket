from dataclasses import dataclass
from typing import Optional
from core.context import SmartMoneySummary
from agents.shared.python.db import get_known_whales

@dataclass
class OnchainScore:
    score: float          # -1.0 ... +1.0, знак = направление рынка
    confidence: float     # 0.0 ... 1.0
    direction: str        # "CONFIRM" | "CONTRA" | "NEUTRAL"
    annotation: str       # Короткий текст для промпта (≤ 80 символов)
    whale_count: int      # Кол-во известныx инсайдеров
    yes_dominance: float
    recent_ratio_2h: float = 0.0


def compute_onchain_score(
    sm: Optional[SmartMoneySummary],
    target_outcome: str = "YES"
) -> OnchainScore:
    """
    Детерминированный числовой скоринг ончейн-данных. Без LLM.
    """
    neutral = OnchainScore(0.0, 0.0, "NEUTRAL", "SmartMoney: нет данных", 0, 0.5, 0.0)
    if sm is None or not sm.available:
        return neutral

    total = sm.total_yes_usd + sm.total_no_usd
    if total < 200:  # слишком мало данных
        return neutral

    dom = sm.yes_dominance  # 0..1

    # 1. Базовый score из доминирования YES-объёма
    raw_score = (dom - 0.5) * 2.0          # -1..+1
    raw_score = max(-1.0, min(1.0, raw_score))

    # 2. Бонус за known whales (учитываем только подтвержденных инсайдеров)
    known = {k.lower(): v for k, v in get_known_whales().items()}
    whale_boost = 0.0
    whale_count = 0

    if getattr(sm, "wallets_list", None):
        for w in sm.wallets_list:
            match = known.get(w.address.lower())
            if not match and w.alias:
                for addr, whale_info in known.items():
                    if whale_info.get("alias") == w.alias:
                        match = whale_info
                        break
            
            is_insider = False
            if match:
                is_insider = bool(match.get("is_insider"))
            if getattr(w, "is_insider", False):
                is_insider = True

            if is_insider:
                wr = (match.get("win_rate") or w.win_rate) if (match or w.win_rate is not None) else 0.0
                if wr > 0.6:
                    whale_boost += 0.15
                elif wr < 0.4:
                    whale_boost -= 0.15
                whale_count += 1
    else:
        # Fallback на парсинг top_wallets для обратной совместимости со старыми тестами
        for line in sm.top_wallets:
            parts = line.strip().split(" | ")
            if not parts:
                continue
            alias_part = parts[0].split(" → ")[0].strip()
            
            match = None
            for addr, whale_info in known.items():
                db_alias = whale_info.get("alias")
                if (db_alias and db_alias == alias_part) or addr.startswith(alias_part.replace("...", "")):
                    match = whale_info
                    break
                    
            if match and match.get("is_insider"):
                wr = match.get("win_rate") or 0.0
                if wr > 0.6:
                    whale_boost += 0.15
                elif wr < 0.4:
                    whale_boost -= 0.15
                whale_count += 1

    final_score = max(-1.0, min(1.0, raw_score + whale_boost))

    # 3. Confidence: растёт с объёмом, числом инсайдеров и недавней активностью
    volume_conf = min(1.0, total / 50_000)   # насыщение на $50k
    whale_conf = min(0.3, whale_count * 0.1)
    
    # confidence буст за недавнюю активность за последние 2 часа
    recent_ratio = getattr(sm, "recent_ratio_2h", 0.0)
    recent_boost = 0.0
    if recent_ratio > 0.3:
        recent_boost = 0.1
    if recent_ratio > 0.6:
        recent_boost = 0.2

    confidence = min(1.0, volume_conf * 0.7 + whale_conf + recent_boost)

    # 4. Direction относительно target_outcome
    if abs(final_score) < 0.1:
        direction = "NEUTRAL"
    elif (final_score > 0 and target_outcome == "YES") or \
         (final_score < 0 and target_outcome == "NO"):
        direction = "CONFIRM"   # совпадает с сигналом
    else:
        direction = "CONTRA"    # противоречит сигналу

    # 5. Аннотация: 1 строка для промпта
    side = "YES" if dom >= 0.5 else "NO"
    vol_k = int(total / 1000)
    wr_note = f", {whale_count} whale(s)" if whale_count else ""
    annotation = (
        f"SmartMoney: {side} dom={dom:.0%}, vol=${vol_k}k, recent_2h={recent_ratio:.0%}{wr_note} → score={final_score:+.2f}"
    )

    return OnchainScore(
        score=final_score,
        confidence=confidence,
        direction=direction,
        annotation=annotation,
        whale_count=whale_count,
        yes_dominance=dom,
        recent_ratio_2h=recent_ratio
    )
