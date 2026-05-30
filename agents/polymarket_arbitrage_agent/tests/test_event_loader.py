import pytest
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels

def test_load_events_with_levels():
    # Загрузим 10 событий с минимальным объемом 1000 чтобы гарантированно что-то найти
    events = load_events_with_levels(limit=10, min_markets_per_event=2, min_volume_per_market=1000)
    
    assert isinstance(events, list)
    
    for event in events:
        assert event.event_slug
        assert event.event_title
        assert len(event.markets) >= 2
        
        # Проверяем, что уровни распарсились и отсортированы
        assert len(event.sorted_markets) >= 2
        
        prev_level = -1
        units = set()
        for m in event.sorted_markets:
            assert m.numeric_level is not None
            assert m.numeric_level >= prev_level
            prev_level = m.numeric_level
            units.add(m.level_unit)
            
        assert len(units) == 1, "Все рынки в событии должны иметь одинаковую единицу измерения"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
