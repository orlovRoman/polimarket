from datetime import datetime, timedelta
from core.price_velocity import detect_velocity_anomaly

def test_detect_velocity_anomaly_flat():
    # Тест на стабильную цену (FLAT)
    now = datetime.now()
    history = [
        {"price": 0.10, "timestamp": now - timedelta(hours=3)},
        {"price": 0.10, "timestamp": now - timedelta(hours=2)},
        {"price": 0.10, "timestamp": now}
    ]
    res = detect_velocity_anomaly(history)
    assert res.has_anomaly is False
    assert res.direction == "FLAT"
    assert res.suspicion == "ORGANIC"

def test_detect_velocity_anomaly_pump():
    # Тест на резкий скачок вверх (+50% за 2 часа)
    now = datetime.now()
    history = [
        {"price": 0.10, "timestamp": now - timedelta(hours=2)},
        {"price": 0.12, "timestamp": now - timedelta(hours=1)},
        {"price": 0.15, "timestamp": now}
    ]
    res = detect_velocity_anomaly(history)
    assert res.has_anomaly is True
    assert res.direction == "UP"
    assert res.suspicion == "PUMP"
    assert "Velocity: +50%" in res.annotation

def test_detect_velocity_anomaly_dump():
    # Тест на резкий провал вниз (-40% за 2 часа)
    now = datetime.now()
    history = [
        {"price": 0.10, "timestamp": now - timedelta(hours=2)},
        {"price": 0.08, "timestamp": now - timedelta(hours=1)},
        {"price": 0.06, "timestamp": now}
    ]
    res = detect_velocity_anomaly(history)
    assert res.has_anomaly is True
    assert res.direction == "DOWN"
    assert res.suspicion == "DUMP"
    assert "Velocity: -40%" in res.annotation
