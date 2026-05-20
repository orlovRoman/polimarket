import os
from datetime import datetime
from pathlib import Path
from typing import Optional

class ObsidianAdapter:
    """
    Адаптер для взаимодействия с файловой системой Obsidian (vault).
    Обеспечивает создание и чтение Markdown файлов в нужных директориями
    в соответствии с 3-уровневой архитектурой памяти проекта.
    """

    def __init__(self, vault_path: str = "/home/orlovrp/vault"):
        self.vault_path = Path(vault_path)
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
        """
        if date is None:
            date = datetime.now()
        
        filename = f"{date.strftime('%Y-%m-%d')}-polimarket-orchestrator.md"
        filepath = self.vault_path / "daily" / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
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

    def promote_to_memory(self, category: str, filename: str, content: str) -> Path:
        """
        Сохраняет долгосрочную память (Layer 3).
        Допустимые категории: 'durable', 'entities', 'market-patterns', 'source-profiles'.
        """
        valid_categories = ["durable", "entities", "market-patterns", "source-profiles"]
        if category not in valid_categories:
            raise ValueError(f"Недопустимая категория памяти. Разрешены: {valid_categories}")

        if not filename.endswith(".md"):
            filename += ".md"

        filepath = self.vault_path / "memory" / category / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
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
            f.write(f"\\n\\n{content}\n")
            
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

if __name__ == "__main__":
    # Тестирование адаптера
    adapter = ObsidianAdapter()
    print(f"Vault structure ensured at: {adapter.vault_path}")
    
    # Создадим тестовый daily summary
    test_content = "# Test Summary\\nThis is a test of the Obsidian adapter."
    path = adapter.write_daily_summary(test_content)
    print(f"Test summary written to: {path}")
