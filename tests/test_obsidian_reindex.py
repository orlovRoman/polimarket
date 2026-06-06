import os
import shutil
import tempfile
from pathlib import Path
import pytest

from agents.shared.utils.obsidian_adapter import ObsidianAdapter, parse_frontmatter
from agents.shared.python.db import get_connection

@pytest.fixture
def temp_vault():
    # Создаем временную директорию для vault
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    # Удаляем после теста
    shutil.rmtree(temp_dir, ignore_errors=True)

def test_parse_frontmatter():
    content = """---
title: Test Note
category: durable
tags: ["tag1", "tag2"]
---
Some body content here."""
    meta, clean = parse_frontmatter(content)
    assert meta["title"] == "Test Note"
    assert meta["category"] == "durable"
    assert meta["tags"] == ["tag1", "tag2"]
    assert clean == "Some body content here."

def test_obsidian_reindex_workflow(temp_vault):
    # Инициализируем адаптер с временным путем
    adapter = ObsidianAdapter(vault_path=str(temp_vault))
    
    # 1. Проверяем, что создалась структура
    assert (temp_vault / "memory/durable").exists()
    
    # Сначала очистим таблицу vault_index в тестовой БД для изоляции
    with get_connection() as conn:
        conn.execute("DELETE FROM vault_index")
    
    # 2. Создаем тестовый .md файл на диске в durable memory
    file_content = """---
title: Cryptocurreny Regulation Trend
category: durable
tags: ["crypto", "regulation"]
---
US SEC has approved something."""
    
    file_path = temp_vault / "memory/durable/crypto-regulation.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(file_content)
        
    # Запускаем переиндексацию
    count = adapter.reindex_all_files()
    assert count == 1
    
    # Проверяем, что в БД появилась запись
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vault_index")
        rows = cursor.fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        # Относительный путь должен быть нормализован с прямыми слэшами
        assert row["path"] == "memory/durable/crypto-regulation.md"
        assert row["category"] == "durable"
        assert row["title"] == "Cryptocurreny Regulation Trend"
        assert "crypto" in row["tags"]
        
    # 3. Изменяем содержимое файла
    new_content = """---
title: Cryptocurreny Regulation Trend Updated
category: durable
tags: ["crypto", "regulation", "sec"]
---
US SEC has approved something new."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    # Переиндексируем
    count = adapter.reindex_all_files()
    assert count == 1
    
    # Проверяем обновления в БД
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vault_index")
        rows = cursor.fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["title"] == "Cryptocurreny Regulation Trend Updated"
        assert "sec" in row["tags"]

    # 4. Удаляем файл с диска
    os.remove(file_path)
    
    # Переиндексируем
    count = adapter.reindex_all_files()
    assert count == 0
    
    # Проверяем, что запись удалилась из БД
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vault_index")
        db_count = cursor.fetchone()[0]
        assert db_count == 0
