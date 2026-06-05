import pytest
from core.context import SmartMoneySummary, WalletInfo
from core.onchain_scorer import compute_onchain_score

def test_scorer_only_insider_gets_boost(monkeypatch):
    # Мокаем известных китов
    # Один инсайдер (p-value значим, is_insider=True)
    # Один везунчик (is_insider=False, хотя win_rate=0.8)
    def mock_get_known_whales():
        return {
            "0xinsider": {
                "alias": "InsiderWhale",
                "win_rate": 0.75,
                "is_insider": True
            },
            "0xlucky": {
                "alias": "LuckyGuy",
                "win_rate": 0.80,
                "is_insider": False
            }
        }

    monkeypatch.setattr("core.onchain_scorer.get_known_whales", mock_get_known_whales)

    # 1. Тестируем кошелек-инсайдер
    sm_insider = SmartMoneySummary(
        available=True,
        total_yes_usd=1000,
        total_no_usd=1000,
        yes_dominance=0.5,
        top_wallets=[],
        summary="",
        wallets_list=[
            WalletInfo(
                address="0xinsider",
                alias="InsiderWhale",
                win_rate=0.75,
                side="YES",
                volume_usd=1000,
                is_insider=True
            )
        ]
    )
    score_insider = compute_onchain_score(sm_insider)
    # Должен быть буст +0.15 за инсайдера (базовый score из dominance=0.5 равен 0.0)
    assert score_insider.whale_count == 1
    assert abs(score_insider.score - 0.15) < 0.01

    # 2. Тестируем кошелек-lucky (не инсайдер)
    sm_lucky = SmartMoneySummary(
        available=True,
        total_yes_usd=1000,
        total_no_usd=1000,
        yes_dominance=0.5,
        top_wallets=[],
        summary="",
        wallets_list=[
            WalletInfo(
                address="0xlucky",
                alias="LuckyGuy",
                win_rate=0.80,
                side="YES",
                volume_usd=1000,
                is_insider=False
            )
        ]
    )
    score_lucky = compute_onchain_score(sm_lucky)
    # Lucky не должен учитываться (whale_count = 0, score = 0.0)
    assert score_lucky.whale_count == 0
    assert abs(score_lucky.score - 0.0) < 0.01


def test_scorer_recent_activity_confidence_boost(monkeypatch):
    monkeypatch.setattr("core.onchain_scorer.get_known_whales", lambda: {})

    # 1. Без недавней активности
    sm_normal = SmartMoneySummary(
        available=True,
        total_yes_usd=10000,
        total_no_usd=10000,
        yes_dominance=0.5,
        top_wallets=[],
        summary="",
        recent_ratio_2h=0.10
    )
    score_normal = compute_onchain_score(sm_normal)

    # 2. С высокой недавней активностью (recent_ratio = 0.35 -> boost +0.1)
    sm_active = SmartMoneySummary(
        available=True,
        total_yes_usd=10000,
        total_no_usd=10000,
        yes_dominance=0.5,
        top_wallets=[],
        summary="",
        recent_ratio_2h=0.35
    )
    score_active = compute_onchain_score(sm_active)

    # Уверенность (confidence) должна вырасти на 0.1
    assert abs(score_active.confidence - score_normal.confidence - 0.1) < 0.01
