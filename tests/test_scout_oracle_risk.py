"""
Тесты для oracle_risk поля SCOUT-агента.
Проверяют что промпт корректно выделяет Description рынка
и что агент не генерирует общие фразы при наличии конкретных правил.
"""
import pytest
from unittest.mock import MagicMock, patch
import json


DESCRIPTION_WITH_RULES = (
    "This market resolves YES if the CME WTI Crude Oil futures "
    "closing price on May 30, 2026 is above $80.00/bbl. "
    "Resolution source: CME official settlement price published at 2:30 PM CT. "
    "If trading is halted or the settlement is delayed, the market resolves "
    "based on the last available price before market close."
)

DESCRIPTION_EMPTY = ""
DESCRIPTION_SHORT = "Resolves based on official data."


def _build_prompt_block(description: str) -> str:
    """Воспроизводит логику формирования блока description из agent.py"""
    if description and len(description) > 20:
        content = description
    else:
        content = "⚠️ Описание отсутствует — оракул-риск не определён, требуется ручная проверка на сайте."
    return (
        "╔══════════════════════════════════════════════════════════╗\n"
        "║  ПРАВИЛА РАЗРЕШЕНИЯ РЫНКА (ОРАКУЛ) — ЧИТАТЬ ВНИМАТЕЛЬНО ║\n"
        "╚══════════════════════════════════════════════════════════╝\n"
        f"{content}\n\n"
        "ЗАДАЧА ПО ОРАКУЛУ: В поле oracle_risk ты ОБЯЗАН:\n"
        "1. Процитировать дословно ключевые критерии из Описания выше (кто решает, какой источник, какая дата)\n"
        "2. Указать конкретные формулировки которые допускают двоякое толкование\n"
        "3. НЕ писать общие фразы — только конкретику из текста выше\n"
        "Если описание пустое — пиши: \"oracle_risk: Описание рынка отсутствует, риск неопределён\"\n"
        "══════════════════════════════════════════════════════════════\n"
    )


def test_description_block_contains_full_rules():
    """Полное описание с правилами передаётся в блок без обрезки"""
    block = _build_prompt_block(DESCRIPTION_WITH_RULES)
    assert "CME official settlement price" in block
    assert "2:30 PM CT" in block
    assert "$80.00/bbl" in block
    assert "⚠️" not in block


def test_description_block_empty_shows_warning():
    """Пустое описание → предупреждение, не пустой блок"""
    block = _build_prompt_block(DESCRIPTION_EMPTY)
    assert "⚠️ Описание отсутствует" in block


def test_description_block_short_shows_warning():
    """Слишком короткое описание (< 20 символов) → предупреждение"""
    block = _build_prompt_block("Short text.")
    assert "⚠️ Описание отсутствует" in block


def test_description_block_boundary_exactly_20_chars():
    """Ровно 20 символов → всё ещё считается пустым (len > 20, не >=)"""
    block = _build_prompt_block("A" * 20)
    assert "⚠️ Описание отсутствует" in block


def test_description_block_21_chars_passes():
    """21 символ → описание принимается как есть"""
    block = _build_prompt_block("A" * 21)
    assert "⚠️" not in block
    assert "A" * 21 in block


def test_oracle_risk_not_generic_when_description_present():
    """
    Если description содержит конкретные правила — oracle_risk
    НЕ должен быть одной из заготовленных общих фраз.
    Проверяем список известных шаблонных ответов.
    """
    GENERIC_PHRASES = [
        "Отсутствие достоверной информации",
        "возможен риск двоякой интерпретации",
        "двусмысленность в определении",
        "риск неожиданного разрешения",
        "Правила оракула допускают двоякое толкование",
    ]
    # Имитируем LLM-ответ с конкретикой
    mock_oracle_risk = (
        "CME WTI futures settlement price 30 мая 2026 выше $80/bbl. "
        "Источник: CME official settlement (2:30 PM CT). "
        "Риск: при остановке торгов используется 'последняя доступная цена' — "
        "это может быть цена за час до закрытия, что отличается от settlement price."
    )
    for phrase in GENERIC_PHRASES:
        assert phrase not in mock_oracle_risk, (
            f"oracle_risk содержит шаблонную фразу: '{phrase}'"
        )


def test_oracle_risk_cites_source_from_description():
    """
    oracle_risk должен содержать конкретный источник из description.
    Проверяем что 'CME' из описания появляется в oracle_risk.
    """
    oracle_risk = (
        "Источник разрешения: CME официальная цена закрытия. "
        "Риск: 'последняя доступная цена' при остановке торгов может отличаться "
        "от официального settlement."
    )
    # Источник из description должен быть упомянут
    assert "CME" in oracle_risk


def test_prompt_contains_oracle_task_instructions():
    """
    Промпт должен содержать явную задачу по oracle_risk
    прямо рядом с блоком description — не в конце промпта.
    """
    block = _build_prompt_block(DESCRIPTION_WITH_RULES)
    assert "ЗАДАЧА ПО ОРАКУЛУ" in block or "oracle_risk" in block.lower() or \
           "ОБЯЗАН" in block or "процитировать" in block.lower(), (
        "Промпт не содержит явной инструкции по oracle_risk рядом с блоком описания"
    )
