import os
import shutil
import tempfile
from pathlib import Path
import pytest
import logging
from unittest.mock import patch, MagicMock

from agents.shared.utils.obsidian_adapter import ObsidianAdapter, parse_frontmatter
from agents.shared.python.db import get_connection

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Каждый тест получает чистую временную БД — не трогает продакшн базу."""
    import agents.shared.python.db as db_module
    test_db_path = tmp_path / "test_nexus.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    monkeypatch.setattr(db_module, "_db_initialized", False)
    monkeypatch.setattr(db_module, "_db_init_failed", False)
    db_module.init_db()
    yield

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

def test_parse_frontmatter_with_url_value():
    """parse_frontmatter корректно обрабатывает числа и URL."""
    content = """---
title: Test
url: https://polymarket.com/event/test
confidence: 0.85
---
Body."""
    meta, clean = parse_frontmatter(content)
    assert "polymarket.com" in meta.get("url", ""), \
        "URL в frontmatter должен парситься без обрезания"
    assert meta.get("confidence") == 0.85
    assert clean == "Body."

def test_obsidian_reindex_workflow(temp_vault):
    # Инициализируем адаптер с временным путем
    adapter = ObsidianAdapter(vault_path=str(temp_vault))
    
    # 1. Проверяем, что создалась структура
    assert (temp_vault / "memory/durable").exists()
    
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
        
    # Запускаем переиндексацию (1 файл добавлен)
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
        
    # Переиндексируем (1 файл обновлен)
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
    
    # Переиндексируем (0 файлов на диске)
    count = adapter.reindex_all_files()
    assert count == 0
    
    # Проверяем, что запись удалилась из БД
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vault_index")
        db_count = cursor.fetchone()[0]
        assert db_count == 0

def test_reindex_unchanged_files_not_double_counted(temp_vault):
    """reindex не считает неизмененные файлы как новые."""
    adapter = ObsidianAdapter(vault_path=str(temp_vault))

    # Создаём файл и индексируем
    file_path = temp_vault / "memory/durable/test.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("---\ntitle: Test\ncategory: durable\ntags: []\n---\nContent.")

    first_run = adapter.reindex_all_files()
    second_run = adapter.reindex_all_files()  # файл не менялся

    # Оба запуска должны видеть 1 файл (сумма actually_indexed + unchanged)
    assert first_run == second_run == 1

def test_promote_to_memory_logs_index_error(temp_vault):
    """promote_to_memory логирует ошибку индексации, а не скрывает."""
    adapter = ObsidianAdapter(vault_path=str(temp_vault))

    from agents.shared.utils.obsidian_adapter import logger as adapter_logger

    with patch.object(adapter_logger, "warning") as mock_warning:
        with patch("agents.shared.python.db.update_vault_index", side_effect=RuntimeError("DB locked")):
            adapter.promote_to_memory("durable", "test.md", "Test content")

    mock_warning.assert_called_once()
    assert "Ошибка индексации" in mock_warning.call_args[0][0], \
        "Ошибка индексации должна попасть в WARNING лог, а не быть проглочена"

def test_promote_to_memory_path_normalized(temp_vault):
    """rel_path в vault_index всегда должен использовать прямые слэши."""
    adapter = ObsidianAdapter(vault_path=str(temp_vault))

    captured_paths = []
    original_update = __import__(
        "agents.shared.python.db", fromlist=["update_vault_index"]
    ).update_vault_index

    def capture_path(path, *args, **kwargs):
        captured_paths.append(path)
        return original_update(path, *args, **kwargs)

    with patch("agents.shared.python.db.update_vault_index", side_effect=capture_path):
        adapter.promote_to_memory("durable", "test-note.md", "Content")

    assert len(captured_paths) == 1
    assert "\\" not in captured_paths[0], \
        f"rel_path содержит обратные слэши (Windows-путь): {captured_paths[0]}"
    assert captured_paths[0] == "memory/durable/test-note.md"
