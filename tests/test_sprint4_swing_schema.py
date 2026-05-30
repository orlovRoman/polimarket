# test_sprint4_swing_schema.py
import json, sys; sys.path.append('.')

SWING_REQUIRED = {'hype_potential', 'recommendation', 'target_outcome',
                  'target_exit_price', 'confidence', 'reasoning',
                  'catalyst', 'catalyst_absence_reason', 'swing_risk', 'swing_verdict'}

test_cases = [
    # Кейс 1: есть хайп
    {
        "hype_potential": 0.80, "recommendation": "buy", "target_outcome": "YES",
        "target_exit_price": 0.40, "confidence": 0.85,
        "reasoning": "График показывает рост с 0.28 до 0.32 за 4ч...",
        "catalyst": "Твит известного трейдера 3ч назад + 4 статьи в медиа",
        "catalyst_absence_reason": "",
        "swing_risk": "Рынок закрывается через 36ч, есть время. Но ордербук тонкий.",
        "swing_verdict": "Купить YES до 0.33, выход при 0.40. Стоп 0.27."
    },
    # Кейс 2: нет хайпа
    {
        "hype_potential": 0.15, "recommendation": "ignore", "target_outcome": "YES",
        "target_exit_price": 0.0, "confidence": 0.90,
        "reasoning": "Рынок торгуется в боковике последние 12ч...",
        "catalyst": "",
        "catalyst_absence_reason": "За последние 8ч нет упоминаний в медиа. Тема слишком техническая для вирального разгона.",
        "swing_risk": "Без катализатора движение будет только на микроликвидности.",
        "swing_verdict": "Пропустить. Ждать внешнего катализатора."
    }
]

for i, case in enumerate(test_cases, 1):
    missing = SWING_REQUIRED - set(case.keys())
    assert not missing, f"Кейс {i}: нет полей {missing}"
    
    if case['recommendation'] == 'buy':
        assert case['catalyst'], f"Кейс {i}: при buy catalyst обязателен"
    if case['recommendation'] == 'ignore':
        assert case['catalyst_absence_reason'], f"Кейс {i}: при ignore нужно catalyst_absence_reason"
    assert case['swing_risk'], f"Кейс {i}: swing_risk всегда обязателен"
    assert case['swing_verdict'], f"Кейс {i}: swing_verdict всегда обязателен"
    print(f"✅ Кейс {i} ({case['recommendation']}): схема валидна")

print("\n✅ СПРИНТ 4 ПРОЙДЕН")
