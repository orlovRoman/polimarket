import sqlite3
from pathlib import Path
import asyncio

db_path = Path("vault/database.sqlite")
if not db_path.exists():
    print("БД не найдена")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== Проверка таблиц коридоров в БД ===")
# 1. Синтетические коридоры
try:
    synth_all = conn.execute("SELECT COUNT(*) as cnt FROM synthetic_corridors").fetchone()['cnt']
    synth_filtered = conn.execute("SELECT COUNT(*) as cnt FROM synthetic_corridors WHERE (status != 'DELETED' OR status IS NULL)").fetchone()['cnt']
    print(f"Синтетические коридоры: всего в БД={synth_all}, отображается в дашборде={synth_filtered}")
    if synth_all > 0:
        sample = conn.execute("SELECT signal_id, event_title, status FROM synthetic_corridors LIMIT 3").fetchall()
        for s in sample:
            print(f"  - ID: {s['signal_id']} | Title: {s['event_title']} | Status: {s['status']}")
except Exception as e:
    print(f"Ошибка чтения synthetic_corridors: {e}")

# 2. Временные коридоры
try:
    temp_all = conn.execute("SELECT COUNT(*) as cnt FROM temporal_corridors").fetchone()['cnt']
    temp_filtered = conn.execute("SELECT COUNT(*) as cnt FROM temporal_corridors WHERE (status != 'DELETED' OR status IS NULL)").fetchone()['cnt']
    print(f"Временные коридоры: всего в БД={temp_all}, отображается в дашборде={temp_filtered}")
    if temp_all > 0:
        sample = conn.execute("SELECT id, event_title, status FROM temporal_corridors LIMIT 3").fetchall()
        for s in sample:
            print(f"  - ID: {s['id']} | Title: {s['event_title']} | Status: {s['status']}")
except Exception as e:
    print(f"Ошибка чтения temporal_corridors: {e}")

conn.close()

# 3. Тестовый запуск сканера
print("\n=== Пробный запуск сканера (Synthetic Corridors) ===")
async def test_scan():
    try:
        from services.synthetic_corridor_scanner import run_synthetic_corridor_scan
        print("Запуск run_synthetic_corridor_scan (limit=50)...")
        found = await asyncio.to_thread(
            run_synthetic_corridor_scan,
            poly_limit=50,
            budget_per_trade=100.0,
            min_volume=100,
            min_executable_contracts=1,
        )
        print(f"Успешно завершено. Найдено коридоров: {len(found)}")
        for f in found[:3]:
            print(f"  - Событие: {f.event_title} | Спред: {f.real_spread_pct:.1%}")
    except Exception as e:
        print(f"Ошибка при сканировании: {e}")

asyncio.run(test_scan())
