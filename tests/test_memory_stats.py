import pytest

def _simulate_get_memory_stats(has_vault_index: bool) -> dict:
    """Симуляция get_memory_stats с/без таблицы vault_index."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE memory (key TEXT, expires_at TEXT)"
    )
    conn.execute("INSERT INTO memory VALUES ('k1', NULL)")
    conn.execute("INSERT INTO memory VALUES ('k2', '2000-01-01T00:00:00')")  # истёк
    if has_vault_index:
        conn.execute("CREATE TABLE vault_index (id INTEGER)")
        conn.execute("INSERT INTO vault_index VALUES (1)")
        conn.execute("INSERT INTO vault_index VALUES (2)")

    stats = {'total_keys': 0, 'expired_keys': 0, 'vault_files': 0}
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM memory")
    stats['total_keys'] = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM memory "
        "WHERE expires_at IS NOT NULL AND expires_at < datetime('now')"
    )
    stats['expired_keys'] = cursor.fetchone()[0]
    try:
        cursor.execute("SELECT COUNT(*) FROM vault_index")
        stats['vault_files'] = cursor.fetchone()[0]
    except Exception:
        stats['vault_files'] = 0
    conn.close()
    return stats


def test_memory_stats_with_vault_index():
    stats = _simulate_get_memory_stats(has_vault_index=True)
    assert stats['total_keys'] == 2
    assert stats['expired_keys'] == 1
    assert stats['vault_files'] == 2

def test_memory_stats_without_vault_index_does_not_raise():
    """Если vault_index не существует — функция не падает, возвращает 0."""
    stats = _simulate_get_memory_stats(has_vault_index=False)
    assert stats['vault_files'] == 0
    assert stats['total_keys'] == 2

def test_memory_stats_all_keys_clean():
    """Если нет истёкших записей — expired_keys == 0."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE memory (key TEXT, expires_at TEXT)")
    conn.execute("INSERT INTO memory VALUES ('k1', NULL)")
    conn.execute("INSERT INTO memory VALUES ('k2', NULL)")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM memory WHERE expires_at IS NOT NULL AND expires_at < datetime('now')")
    expired = cursor.fetchone()[0]
    conn.close()
    assert expired == 0
