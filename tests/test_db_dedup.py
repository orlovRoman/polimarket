import pytest
from datetime import datetime
from agents.shared.python.db import init_db, is_alert_already_sent, mark_alert_sent, get_connection

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM sent_alerts")
        conn.execute("DELETE FROM signals")

def test_dedup_logic():
    alert_key = "test_alert_123"
    
    assert not is_alert_already_sent(alert_key, ttl_hours=12)
    
    mark_alert_sent(alert_key, "synthetic_corridor")
    
    assert is_alert_already_sent(alert_key, ttl_hours=12)

def test_pending_signals_dedup():
    with get_connection() as conn:
        cursor = conn.cursor()
        # Drop the unique index temporarily to insert duplicates
        cursor.execute("DROP INDEX IF EXISTS idx_signals_market_pending")
        
        # Insert duplicate PENDING signals for the same market_id
        cursor.execute("""
            INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
            VALUES 
                ('sig1', 'whale', 'market_dup', 'polymarket', 0.8, 'HIGH', 'sum1', 'det1', 'PENDING'),
                ('sig2', 'whale', 'market_dup', 'polymarket', 0.9, 'HIGH', 'sum2', 'det2', 'pending'),
                ('sig3', 'whale', 'market_dup', 'polymarket', 0.7, 'HIGH', 'sum3', 'det3', ' PENDING ')
        """)
        
        # Insert a non-pending signal for the same market_id
        cursor.execute("""
            INSERT INTO signals (id, type, market_id, platform, confidence, priority, summary, details, status)
            VALUES ('sig4', 'whale', 'market_dup', 'polymarket', 0.6, 'MEDIUM', 'sum4', 'det4', 'ACTIVE')
        """)

    # Reset initialization flag to force init_db to run again
    import agents.shared.python.db as db
    db._db_initialized = False
    
    # Run init_db which should resolve conflicts and re-create the unique index
    init_db()
    
    # Verify the database state
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status FROM signals WHERE market_id = 'market_dup' ORDER BY id")
        rows = {r[0]: r[1] for r in cursor.fetchall()}
        
        # Only the MAX(id) of PENDING signals (which is 'sig3') should remain PENDING
        # Note: 'sig3' was ' PENDING ' which trims to 'PENDING'
        # 'sig1' and 'sig2' should be archived
        # 'sig4' ('ACTIVE') should remain unchanged
        assert rows['sig1'] == 'ARCHIVED'
        assert rows['sig2'] == 'ARCHIVED'
        assert rows['sig3'] == 'PENDING'
        assert rows['sig4'] == 'ACTIVE'
        
        # Verify that unique index exists and works
        cursor.execute("PRAGMA index_list(signals)")
        indexes = {idx[1]: idx[2] for idx in cursor.fetchall()}
        assert 'idx_signals_market_pending' in indexes
        assert indexes['idx_signals_market_pending'] == 1  # 1 means unique
