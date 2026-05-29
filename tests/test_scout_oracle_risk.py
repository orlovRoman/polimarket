# tests/test_scout_oracle_risk.py

def test_scout_prompt_contains_oracle_block(monkeypatch):
    """Промпт содержит блок ПРАВИЛА РАЗРЕШЕНИЯ, а не просто 'Описание:'"""
    # Подготовим минимальный market с description
    from types import SimpleNamespace
    from datetime import datetime
    market = SimpleNamespace(
        id="test_id", title="Will oil close above $80?",
        description="Resolves YES if CME WTI settlement price on May 30 > $80/bbl. Source: CME official.",
        outcome="YES", price=0.45,
        close_time=datetime(2026, 5, 30), platform="polymarket"
    )
    from agents.shared.utils.prompt_guards import guard_description
    block = guard_description(market.description)
    assert "ПРАВИЛА РАЗРЕШЕНИЯ РЫНКА" in block
    assert "CME" in block
    assert "ЗАДАЧА ПО ОРАКУЛУ" in block
    # Старая форма НЕ должна присутствовать
    assert "Описание:" not in block

def test_scout_prompt_no_duplicate_oracle_rule():
    """В промпте нет дублирующего правила про oracle_risk в конце"""
    import inspect
    from agents.polymarket_mispricing_agent.src import agent as scout_module
    source = inspect.getsource(scout_module)
    # Старое правило должно быть удалено
    assert "Внимательно вчитайся в правила разрешения рынка (Описание)" not in source

def test_scout_oracle_risk_empty_description():
    """При пустом description oracle_risk содержит 'отсутствует', не фантазию"""
    from agents.shared.utils.prompt_guards import guard_description
    block = guard_description("")
    assert "НЕ ПРИДУМЫВАЙ" in block
    assert "oracle_risk" in block.lower() or "оракул-риск" in block.lower()
