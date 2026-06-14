import sys
import os

# Добавляем корень проекта в sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.shared.python.db import get_connection

with get_connection() as conn:
    cur = conn.cursor()

    cur.execute("SELECT count(*) as cnt FROM compound_opportunities WHERE outcome != actual_outcome AND actual_outcome IS NOT NULL;")
    losses = cur.fetchone()["cnt"]

    cur.execute("SELECT count(*) as cnt FROM compound_opportunities WHERE actual_outcome IS NOT NULL;")
    total_resolved = cur.fetchone()["cnt"]

    print(f"Losses in compound_opportunities: {losses} / {total_resolved}")

    cur.execute("SELECT status, count(*) as cnt FROM compound_opportunities GROUP BY status;")
    print("Statuses in compound_opportunities:", dict(cur.fetchall()))

    cur.execute("SELECT status, was_profitable, count(*) as cnt FROM signals WHERE strategy_type LIKE '%compound%' GROUP BY status, was_profitable;")
    print("Signals favourite_compound:", [dict(r) for r in cur.fetchall()])
