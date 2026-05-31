import re
from dataclasses import dataclass


@dataclass
class CatalystVerification:
    confirmed: bool          # True если катализатор найден в новостях
    overlap_words: list[str] # совпавшие ключевые слова
    confidence_penalty: float  # сколько вычесть из confidence (0.0–0.3)
    warning: str             # текст предупреждения или ""


# Стоп-слова, не несущие смысла
_STOPWORDS = {
    "и", "в", "на", "с", "по", "не", "из", "что", "это", "как", "но", "от",
    "для", "до", "при", "за", "к", "о", "а", "the", "a", "an", "of", "in",
    "is", "to", "for", "with", "will", "be", "has", "нет", "может", "если"
}

# Фразы, означающие "катализатора нет" — верификация не нужна
_NO_CATALYST_PHRASES = [
    "нет катализатора", "отсутствует", "не найден", "не обнаружен",
    "катализатор отсутствует", "нет данных"
]


def _extract_keywords(text: str, min_len: int = 3) -> set[str]:
    words = re.findall(r'[а-яёa-z]+', text.lower())
    abbrevs = re.findall(r'\b[A-ZА-Я]{2,5}\b', text)  # ИИ, США, НАТО, FOMC
    abbrevs_lower = [a.lower() for a in abbrevs]
    result = {w for w in words if len(w) >= min_len and w not in _STOPWORDS}
    result |= {a for a in abbrevs_lower if a not in _STOPWORDS}
    return result



def verify_catalyst(
    catalyst: str,
    news_block: str,
    grounded_context: str = "",
    min_overlap: int = 2,
) -> CatalystVerification:
    """
    Проверяет, что слова из catalyst встречаются в новостях.
    Не требует точного совпадения — ищет пересечение ключевых слов.
    """
    if not catalyst or not catalyst.strip():
        return CatalystVerification(
            confirmed=False,
            overlap_words=[],
            confidence_penalty=0.10,
            warning="Катализатор пустой"
        )

    catalyst_lower = catalyst.lower()

    # Если явно сказано "нет катализатора" — это честный ответ, не штрафуем
    if any(phrase in catalyst_lower for phrase in _NO_CATALYST_PHRASES):
        return CatalystVerification(
            confirmed=True,  # честный "нет" подтверждён
            overlap_words=[],
            confidence_penalty=0.0,
            warning=""
        )

    catalyst_kw = _extract_keywords(catalyst)
    all_news_text = (news_block or "") + " " + (grounded_context or "")
    news_kw = _extract_keywords(all_news_text)

    overlap = list(catalyst_kw & news_kw)

    if len(overlap) >= min_overlap:
        return CatalystVerification(
            confirmed=True,
            overlap_words=overlap[:5],
            confidence_penalty=0.0,
            warning=""
        )

    # Катализатор не подтверждён новостями
    penalty = 0.15 if len(overlap) == 1 else 0.25
    return CatalystVerification(
        confirmed=False,
        overlap_words=overlap,
        confidence_penalty=penalty,
        warning=(
            f"⚠️ Катализатор не подтверждён новостями "
            f"(совпадений: {len(overlap)}/{len(catalyst_kw)} слов)"
        )
    )
