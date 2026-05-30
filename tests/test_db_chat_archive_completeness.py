import pytest
import json
from agents.shared.python.db import init_db, get_connection, compress_and_cleanup_chat_history

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM chat_history")
        conn.execute("DELETE FROM memory")

def test_chat_archive_covers_all_old_messages():
    chat_id = 987654321
    
    # 1. Insert 80 messages
    with get_connection() as conn:
        for i in range(80):
            conn.execute(
                "INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)",
                (chat_id, "user" if i % 2 == 0 else "assistant", f"message_{i}")
            )
            
    # 2. Run compression (threshold = 40, keep_last = 20)
    # This should archive 80 - 20 = 60 messages!
    compress_and_cleanup_chat_history(chat_id, keep_last=20, summarize_threshold=40)
    
    # 3. Verify that the remaining chat_history is exactly keep_last=20
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM chat_history WHERE chat_id = ?", (chat_id,))
        count = cursor.fetchone()[0]
        assert count == 20
        
        # 4. Verify the archive in memory has all 60 messages (at least 15+ to prove no [:10] truncation!)
        cursor.execute("SELECT value FROM memory WHERE category = 'episodic' AND key LIKE ?", (f"chat_archive_{chat_id}_%",))
        row = cursor.fetchone()
        assert row is not None, "Ошибка: архив не сохранен в memory!"
        
        archive_text = row[0]
        # Our format is json-serialized string or raw value?
        # Let's check how save_memory saves value.
        # save_memory(key, value) saves value as a JSON string:
        # conn.execute("INSERT OR REPLACE INTO memory (key, value, category, priority, ttl, expires_at) ...")
        # In db.py, row['value'] has the JSON-serialized value of the memory fact.
        # Let's load the JSON if it is serialized.
        try:
            archive_data = json.loads(archive_text)
        except Exception:
            archive_data = archive_text
            
        archived_count = archive_data.count("message_")
        # 60 messages should be archived, meaning we should find "message_" 60 times!
        assert archived_count >= 15, f"Ошибка: в архиве только {archived_count} сообщений из 80!"
        assert archived_count == 60, f"Ошибка: архив содержит {archived_count} сообщений вместо 60!"
