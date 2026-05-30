import pytest
from services.onchain_trend_alert import scan_volume_spikes

def test_scan_volume_spikes_marks_alert_sent(monkeypatch):
    """После добавления спайка в результат — он должен быть помечен как отправленный."""
    sent_keys = []
    
    # Мокаем is_alert_already_sent, чтобы он возвращал False
    monkeypatch.setattr(
        "services.onchain_trend_alert.is_alert_already_sent",
        lambda key, **kw: False
    )
    
    monkeypatch.setattr(
        "services.onchain_trend_alert.mark_alert_sent",
        lambda key, *args, **kw: sent_keys.append(key)
    )
    
    # Мокаем get_connection(), возвращающий 1 спайк
    class DummyCursor:
        def fetchall(self):
            return [{
                "market_id": "m1",
                "title": "Arsenal FC",
                "url": "https://polymarket.com/event/ucl-psg-ars",
                "price": 0.5,
                "vol_recent": 1500.0,
                "vol_prev": 200.0,
                "yes_vol": 1000.0,
                "no_vol": 500.0
            }]
            
    class DummyConn:
        def execute(self, sql, params=()):
            return DummyCursor()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
            
    monkeypatch.setattr("services.onchain_trend_alert.get_connection", lambda: DummyConn())
    
    result = scan_volume_spikes()
    assert len(result) == 1
    assert "onchain_spike_m1" in sent_keys
