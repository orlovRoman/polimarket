import sqlite3

conn = sqlite3.connect('/home/orlovrp/polymarket-bot/vault/database.sqlite')
cursor = conn.cursor()

# 1. Сначала узнаем сколько всего сигналов
cursor.execute("SELECT COUNT(*) FROM signals")
print(f"Total signals before cleanup: {cursor.fetchone()[0]}")

# 2. Удаляем дубликаты, оставляя только самый свежий сигнал для каждой комбинации (market_id, strategy_type, target_outcome)
# В SQLite можно удалить строки, id которых не входит в список максимальных (самых свежих) id для каждой группы
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
AND status = 'PENDING'
"""

cursor.execute(delete_query)
print(f"Deleted {cursor.rowcount} duplicate pending signals.")

conn.commit()

cursor.execute("SELECT COUNT(*) FROM signals")
print(f"Total signals after cleanup: {cursor.fetchone()[0]}")

conn.close()
