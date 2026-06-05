import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Импортируем путь из единого конфига
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import VAULT_PATH

class ObsidianAdapter:
    """
    Адаптер для взаимодействия с файловой системой Obsidian (vault).
    Обеспечивает создание и чтение Markdown файлов в нужных директориями
    в соответствии с 3-уровневой архитектурой памяти проекта.
    """

    def __init__(self, vault_path: str = None):
        self.vault_path = Path(vault_path) if vault_path else VAULT_PATH
        self._ensure_directories()

    def _ensure_directories(self):
        """Создает необходимую структуру папок, если она отсутствует."""
        directories = [
            "inbox/telegram",
            "daily",
            "projects/polymarket",
            "memory/durable",
            "memory/entities",
            "memory/market-patterns",
            "memory/source-profiles"
        ]
        for dir_name in directories:
            (self.vault_path / dir_name).mkdir(parents=True, exist_ok=True)

    def write_daily_summary(self, content: str, date: Optional[datetime] = None) -> Path:
        """
        Записывает ежедневный отчет оркестратора (Daily Summary).
        Формат файла: YYYY-MM-DD-polimarket-orchestrator.md
        Использует режим append, чтобы не потерять данные при повторном вызове за день.
        """
        if date is None:
            date = datetime.now()
        
        filename = f"{date.strftime('%Y-%m-%d')}-polimarket-orchestrator.md"
        filepath = self.vault_path / "daily" / filename
        
        # Добавляем timestamp-разделитель при дописывании
        separator = f"\n\n---\n### Обновление {date.strftime('%H:%M:%S')}\n\n"
        
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(separator + content)
            
        return filepath

    def write_to_inbox(self, filename: str, content: str) -> Path:
        """
        Сохраняет сырые данные, идеи или ссылки в inbox (например, из Telegram).
        """
        if not filename.endswith(".md"):
            filename += ".md"
            
        filepath = self.vault_path / "inbox" / "telegram" / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        return filepath

    def promote_to_memory(self, category: str, filename: str, content: str, tags: list = None) -> Path:
        """
        Сохраняет долгосрочную память (Layer 3) с YAML frontmatter.
        Автоматически индексирует файл в SQLite vault_index для быстрого поиска.
        Допустимые категории: 'durable', 'entities', 'market-patterns', 'source-profiles'.
        """
        valid_categories = ["durable", "entities", "market-patterns", "source-profiles"]
        if category not in valid_categories:
            raise ValueError(f"Недопустимая категория памяти. Разрешены: {valid_categories}")

        if not filename.endswith(".md"):
            filename += ".md"

        filepath = self.vault_path / "memory" / category / filename
        
        import json
        now = datetime.now()
        
        if filepath.exists():
            # Если файл существует, дописываем в конец
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n\n---\n*Добавлено {now.strftime('%Y-%m-%d %H:%M')}*:\n\n{content}")
            
            with open(filepath, "r", encoding="utf-8") as f:
                full_content = f.read()
        else:
            # Создаем новый файл с frontmatter
            frontmatter = (
                f"---\n"
                f"created: {now.isoformat()}\n"
                f"category: {category}\n"
                f"tags: {json.dumps(tags or [])}\n"
                f"---\n\n"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(frontmatter + content)
            full_content = frontmatter + content
        
        # Индексируем в SQLite для быстрого поиска (Layer 1 ↔ Layer 3 связь)
        try:
            import hashlib
            content_hash = hashlib.md5(full_content.encode()).hexdigest()
            from agents.shared.python.db import update_vault_index
            rel_path = str(filepath.relative_to(self.vault_path))
            update_vault_index(rel_path, category, filename.replace(".md", ""), tags, content_hash)
        except Exception:
            pass  # Не блокируем запись, если индексация не удалась
            
        return filepath

    def append_to_project_notes(self, filename: str, content: str) -> Path:
        """
        Добавляет записи (или создает новый файл) в проекты/стратегии.
        """
        if not filename.endswith(".md"):
            filename += ".md"

        filepath = self.vault_path / "projects" / "polymarket" / filename
        
        # Режим 'a' для добавления, если файл существует
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n\n{content}\n")
            
        return filepath

    def read_file(self, relative_path: str) -> Optional[str]:
        """
        Читает содержимое файла из vault по относительному пути (например, 'daily/2026-05-20-polimarket-orchestrator.md').
        """
        filepath = self.vault_path / relative_path
        if filepath.exists() and filepath.is_file():
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def list_files(self, sub_dir: str = "") -> list:
        """
        Возвращает список всех файлов в указанной поддиректории vault.
        """
        target_dir = self.vault_path / sub_dir
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        
        files = []
        for p in target_dir.glob("**/*"):
            if p.is_file():
                # Возвращаем относительный путь от корня vault
                files.append(str(p.relative_to(self.vault_path)))
        return sorted(files)

if __name__ == "__main__":
    # Тестирование адаптера
    adapter = ObsidianAdapter()
    print(f"Vault structure ensured at: {adapter.vault_path}")
    
    # Создадим тестовый daily summary
    test_content = "# Test Summary\\nThis is a test of the Obsidian adapter."
    path = adapter.write_daily_summary(test_content)
    print(f"Test summary written to: {path}")
