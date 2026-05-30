import sqlite3
from datetime import datetime, timezone, timedelta

def test_pending_dedup_keeps_latest_by_created_at(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE signals (
            id TEXT PRIMARY KEY,
            market_id TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    now = datetime.now(timezone.utc)
    
    # Намеренно создаём ситуацию: поздний сигнал имеет меньший UUID лексикографически
    id_old  = "f000-old"  # лексикографически БОЛЬШЕ
    id_new  = "a999-new"  # лексикографически МЕНЬШЕ, но создан ПОЗЖЕ
    
    conn.execute("INSERT INTO signals VALUES (?, 'mkt1', 'PENDING', ?)",
                 (id_old, (now - timedelta(hours=2)).isoformat()))
    conn.execute("INSERT INTO signals VALUES (?, 'mkt1', 'PENDING', ?)",
                 (id_new, now.isoformat()))
    conn.commit()

    # Применяем новую логику дедупликации по created_at
    conn.execute("""
        UPDATE signals 
        SET status = 'ARCHIVED' 
        WHERE status = 'PENDING' 
          AND id NOT IN (
              SELECT id 
              FROM signals s1
              WHERE status = 'PENDING'
                AND id = (
                    SELECT id 
                    FROM signals 
                    WHERE market_id = s1.market_id 
                      AND status = 'PENDING' 
                    ORDER BY created_at DESC, id DESC 
                    LIMIT 1
                )
          )
    """)
    conn.commit()
    
    rows = conn.execute("SELECT id, status FROM signals").fetchall()
    pending = [r for r in rows if r[1] == 'PENDING']
    
    assert len(pending) == 1
    assert pending[0][0] == id_new, f"Ошибка: сохранен старый сигнал {pending[0][0]!r} вместо нового {id_new!r}"
    
    conn.close()
