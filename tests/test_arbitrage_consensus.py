from datetime import datetime
from core.models import ArbitrageSignal
from core.workflow import process_arbitrage_signal

def test_process_arbitrage_signal_success(monkeypatch):
    # Данные сигнала
    arb_sig = ArbitrageSignal(
        id="sig-arb-cross-test-123456",
        type="CROSS_PLATFORM",
        market_id_a="market-a",
        market_id_b="market-b",
        platform_a="polymarket",
        platform_b="kalshi",
        spread_pct=8.5,
        target_outcome="YES_A",
        max_safe_size=150.0,
        edge=0.085,
        confidence=0.85,
        summary="Test Arbitrage Summary",
        details="Test Arbitrage Details",
        status="PENDING",
        created_at=datetime.now()
    )
    
    # Мок стаканов с хорошей ликвидностью
    ob_a = {"spread": 0.01, "bid_depth_5": 100.0, "ask_depth_5": 100.0}
    ob_b = {"spread": 0.01, "bid_depth_5": 200.0, "ask_depth_5": 200.0}
    
    saved_signals = []
    
    # Мокаем save_arbitrage_signal_to_db из db.py
    def mock_save_arb_signal(signal):
        saved_signals.append(signal)
        return True
        
    monkeypatch.setattr("agents.shared.python.db.save_arbitrage_signal_to_db", mock_save_arb_signal)
    
    callback_messages = []
    def mock_callback(msg):
        callback_messages.append(msg)
        
    process_arbitrage_signal(arb_sig, ob_a, ob_b, mock_callback)
    
    assert len(saved_signals) == 1
    assert saved_signals[0].id == arb_sig.id
    assert saved_signals[0].type == "CROSS_PLATFORM"
    assert saved_signals[0].edge == 0.085
    assert len(callback_messages) == 1
    assert "Test Arbitrage Summary" in callback_messages[0]

def test_process_arbitrage_signal_low_liquidity(monkeypatch):
    # Данные сигнала
    arb_sig = ArbitrageSignal(
        id="sig-arb-cross-test-empty",
        type="CROSS_PLATFORM",
        market_id_a="market-a",
        platform_a="polymarket",
        spread_pct=5.0,
        target_outcome="YES_A",
        max_safe_size=50.0,
        edge=0.05,
        confidence=0.75,
        summary="Low Liquidity Arb",
        details="Details",
        status="PENDING",
        created_at=datetime.now()
    )
    
    # Пустой стакан A (нет ликвидности)
    ob_a = {}
    
    saved_signals = []
    def mock_save_arb_signal(signal):
        saved_signals.append(signal)
        return True
        
    monkeypatch.setattr("agents.shared.python.db.save_arbitrage_signal_to_db", mock_save_arb_signal)
    
    callback_messages = []
    def mock_callback(msg):
        callback_messages.append(msg)
        
    process_arbitrage_signal(arb_sig, ob_a, {}, mock_callback)
    
    # Сигнал должен быть отклонен по ликвидности
    assert len(saved_signals) == 0
    assert len(callback_messages) == 0
