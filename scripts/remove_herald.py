import sqlite3
import os
import sys

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import DB_PATH

def remove_herald_from_db():
    if not os.path.exists(DB_PATH):
        print(f"База данных не найдена: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT count(*) FROM agent_opinions WHERE agent_name = 'HERALD'")
        count = cursor.fetchone()[0]
        
        if count > 0:
            print(f"Найдено {count} записей HERALD в agent_opinions. Удаляю...")
            cursor.execute("DELETE FROM agent_opinions WHERE agent_name = 'HERALD'")
            conn.commit()
            print("Успешно удалено.")
        else:
            print("Записей HERALD в agent_opinions не найдено.")
            
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        print(f"Проверка целостности БД: {integrity}")
        
    except Exception as e:
        print(f"Ошибка при удалении: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    remove_herald_from_db()
