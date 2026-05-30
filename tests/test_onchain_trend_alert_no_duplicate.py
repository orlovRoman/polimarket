import pytest
from services.onchain_trend_alert import scan_volume_spikes

def test_scan_volume_spikes_no_duplicate_alert(monkeypatch):
    """Повторный вызов scan_volume_spikes() не возвращает уже отмеченный спайк."""
    sent_keys = set()
    
    # Мокаем is_alert_already_sent и mark_alert_sent
    monkeypatch.setattr(
        "services.onchain_trend_alert.is_alert_already_sent",
        lambda key, **kw: key in sent_keys
    )
    
    monkeypatch.setattr(
        "services.onchain_trend_alert.mark_alert_sent",
        lambda key, *args, **kw: sent_keys.add(key)
    )
    
    # Мокаем get_connection(), возвращающий 1 спайк
    class DummyCursor:
        def fetchall(self):
            return [{
                "market_id": "m2",
                "title": "Chelsea FC",
                "url": "https://polymarket.com/event/ucl-che-ars",
                "price": 0.45,
                "vol_recent": 1200.0,
                "vol_prev": 150.0,
                "yes_vol": 800.0,
                "no_vol": 400.0
            }]
            
    class DummyConn:
        def execute(self, sql, params=()):
            return DummyCursor()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
            
    monkeypatch.setattr("services.onchain_trend_alert.get_connection", lambda: DummyConn())
    
    # 1й вызов → возвращает 1 спайк
    spikes1 = scan_volume_spikes()
    assert len(spikes1) == 1
    assert spikes1[0]["market_id"] == "m2"
    
    # 2й вызов (TTL ещё не истёк) -> должен вернуть 0 спайков, т.к. помечен
    spikes2 = scan_volume_spikes()
    assert len(spikes2) == 0
