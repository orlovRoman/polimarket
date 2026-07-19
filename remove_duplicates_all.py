import sqlite3

conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
cursor = conn.cursor()

# 1. Узнаем сколько всего сигналов
cursor.execute("SELECT COUNT(*) FROM signals")
print(f"Total signals before cleanup: {cursor.fetchone()[0]}")

# 2. Удаляем ВСЕ дубликаты (включая ARCHIVED), оставляя только самый свежий
delete_query = """
DELETE FROM signals
WHERE id NOT IN (
    SELECT id
    FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY market_id, COALESCE(strategy_type, 'SCOUT'), target_outcome
                   ORDER BY created_at DESC
               ) as rn
        FROM signals
    ) sub
    WHERE rn = 1
)
"""

cursor.execute(delete_query)
print(f"Deleted {cursor.rowcount} duplicate signals (including ARCHIVED).")

conn.commit()

cursor.execute("SELECT COUNT(*) FROM signals")
print(f"Total signals after cleanup: {cursor.fetchone()[0]}")

conn.close()
