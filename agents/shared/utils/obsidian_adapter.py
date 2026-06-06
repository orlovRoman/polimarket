import os
import sys
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("NexusPolyBot.ObsidianAdapter")

# Импортируем путь из единого конфига
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import VAULT_PATH

class ObsidianAdapter:
    """
    Адаптер для взаимодействия с файловой системой Obsidian (vault).
    Обеспечивает создание и чтение Markdown файлов в нужных директориях
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
        
        try:
            import hashlib
            content_hash = hashlib.md5(full_content.encode()).hexdigest()
            from agents.shared.python.db import update_vault_index
            rel_path = str(filepath.relative_to(self.vault_path)).replace("\\", "/")
            update_vault_index(rel_path, category, filename.replace(".md", ""), tags, content_hash)
        except Exception as e:
            logger.warning(f"[ObsidianAdapter] Ошибка индексации при promote_to_memory: {e}")
            
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

    def reindex_all_files(self) -> int:
        """
        Проводит полную инкрементальную переиндексацию директории vault/.
        Обнаруживает новые, измененные и удаленные файлы.
        Возвращает количество проиндексированных на диске файлов.
        """
        import hashlib
        from agents.shared.python.db import get_connection, update_vault_index, delete_vault_index

        # 1. Получаем список всех существующих .md файлов на диске
        all_files = self.list_files()
        md_files = [f for f in all_files if f.endswith(".md")]
        md_files_set = set(md_files)

        # 2. Получаем текущее состояние индекса из БД
        db_files = {}
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT path, content_hash FROM vault_index")
                db_files = {row["path"]: row["content_hash"] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"[ObsidianAdapter] Ошибка чтения vault_index: {e}")
            return 0

        # 3. Удаляем из БД файлы, которых больше нет на диске
        deleted_count = 0
        for db_path in list(db_files.keys()):
            # В Windows пути могут быть с обратным слэшем, нормализуем под системные/относительные разделители
            normalized_db_path = db_path.replace("\\", "/")
            normalized_md_files_set = {f.replace("\\", "/") for f in md_files_set}
            if normalized_db_path not in normalized_md_files_set:
                try:
                    delete_vault_index(db_path)
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[ObsidianAdapter] Ошибка удаления индекса для {db_path}: {e}")
        if deleted_count > 0:
            logger.info(f"[ObsidianAdapter] Удалено из индекса: {deleted_count} файлов.")

        # 4. Сканируем и индексируем новые/изменившиеся файлы
        actually_indexed = 0
        unchanged = 0
        for rel_path in md_files:
            # Нормализуем путь для записи в БД (слэши в единый стиль)
            db_rel_path = rel_path.replace("\\", "/")
            filepath = self.vault_path / rel_path
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Вычисляем хеш
                content_hash = hashlib.md5(content.encode()).hexdigest()
                
                # Если файл уже проиндексирован и не менялся — пропускаем
                if db_rel_path in db_files and db_files[db_rel_path] == content_hash:
                    unchanged += 1
                    continue

                # Разбираем frontmatter
                meta, _ = parse_frontmatter(content)
                
                # Определяем категорию
                category = meta.get("category")
                if not category:
                    parts = Path(rel_path).parts
                    if len(parts) > 1:
                        if parts[0] == "memory" and len(parts) > 2:
                            category = parts[1]  # durable, entities, market-patterns, source-profiles
                        else:
                            category = parts[0]  # daily, projects, inbox
                    category = category or "general"

                # Определяем заголовок
                title = meta.get("title") or Path(rel_path).stem.replace("-", " ").replace("_", " ").title()

                # Определяем теги
                tags = meta.get("tags")
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                elif not isinstance(tags, list):
                    tags = []

                # Обновляем индекс в БД
                update_vault_index(db_rel_path, category, title, tags, content_hash)
                actually_indexed += 1
            except Exception as e:
                logger.error(f"[ObsidianAdapter] Ошибка индексации файла {rel_path}: {e}")

        logger.info(
            f"[ObsidianAdapter] Синхронизация завершена. "
            f"Новых/измененных: {actually_indexed}, без изменений: {unchanged}, удалено: {deleted_count}"
        )
        return actually_indexed + unchanged


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Разбирает YAML frontmatter в начале файла.
    Возвращает словарь метаданных и очищенное содержимое.
    """
    meta = {}
    clean_content = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_part = parts[1]
            clean_content = parts[2].strip()
            
            try:
                import yaml
                meta = yaml.safe_load(yaml_part) or {}
                if not isinstance(meta, dict):
                    meta = {}
                return meta, clean_content
            except (ImportError, Exception):
                pass
                
            for line in yaml_part.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip()
                    
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                        
                    if v.startswith("[") and v.endswith("]"):
                        try:
                            import json
                            v = json.loads(v)
                        except Exception:
                            v = [t.strip() for t in v[1:-1].split(",") if t.strip()]
                    else:
                        try:
                            if "." in v:
                                v = float(v)
                            else:
                                v = int(v)
                        except ValueError:
                            pass
                    meta[k] = v
    return meta, clean_content


if __name__ == "__main__":
    # Тестирование адаптера
    adapter = ObsidianAdapter()
    print(f"Vault structure ensured at: {adapter.vault_path}")
    
    # Создадим тестовый daily summary
    test_content = "# Test Summary\\nThis is a test of the Obsidian adapter."
    path = adapter.write_daily_summary(test_content)
    print(f"Test summary written to: {path}")
