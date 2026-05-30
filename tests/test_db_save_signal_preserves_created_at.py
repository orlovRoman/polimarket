import pytest
from datetime import datetime, timezone, timedelta
from core.models import Signal
from agents.shared.python.db import init_db, get_connection, save_signal

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM markets")
        
        # Insert a dummy market to satisfy the foreign key constraint
        conn.execute("""
            INSERT INTO markets (id, platform, title, url, outcome, price, close_time)
            VALUES ('mkt1', 'polymarket', 'Test Market', 'http://test', 'YES', 0.5, '2026-12-31T23:59:59Z')
        """)

def test_save_signal_preserves_original_created_at():
    sig_id = "test_sig_unique_123"
    original_time = datetime.now(timezone.utc) - timedelta(hours=5)
    
    # 1. Save the original signal (using lowercase for pydantic literal validation: 'low', 'medium', 'high')
    sig1 = Signal(
        id=sig_id,
        type="whale",
        market_id="mkt1",
        platform="polymarket",
        edge=0.1,
        confidence=0.8,
        priority="high",
        summary="Original summary",
        details="Original details",
        created_at=original_time
    )
    sig1.status = "PENDING"
    
    assert save_signal(sig1) is True
    
    # Verify the initial save
    with get_connection() as conn:
        row = conn.execute("SELECT created_at, summary FROM signals WHERE id = ?", (sig_id,)).fetchone()
        # SQLite returns the string representation we saved
        assert row["summary"] == "Original summary"
        assert row["created_at"] == original_time.isoformat()
        
    # 2. Save the updated signal with a new timestamp and updated summary
    updated_time = datetime.now(timezone.utc)
    sig2 = Signal(
        id=sig_id,
        type="whale",
        market_id="mkt1",
        platform="polymarket",
        edge=0.15,
        confidence=0.9,
        priority="high",
        summary="Updated summary",
        details="Updated details",
        created_at=updated_time
    )
    sig2.status = "PENDING"
    
    # save_signal returns True if a row was affected (inserted/updated)
    assert save_signal(sig2) is True
    
    # Verify that created_at was PRESERVED as original_time, but summary was UPDATED
    with get_connection() as conn:
        row = conn.execute("SELECT created_at, summary FROM signals WHERE id = ?", (sig_id,)).fetchone()
        assert row["summary"] == "Updated summary"
        assert row["created_at"] == original_time.isoformat(), "Ошибка: created_at был перезаписан!"
