import os
import json
import requests
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from agents.shared.utils.database import DatabaseManager
from agents.shared.utils.obsidian_adapter import ObsidianAdapter

class NexusAgent:
    """
    Оркестратор (NexusAgent) — центральный мозг системы.
    Использует Gemini API для координации специализированных агентов, 
    генерации сводных отчетов и управления долгосрочной памятью в Obsidian.
    """

    def __init__(self, model_name: str = "gemini-2.5-pro", api_key: Optional[str] = None):
        """
        Инициализация агента-координатора.
        """
        self.model_name = model_name
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Критическая ошибка: GOOGLE_API_KEY не найден")
        
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
        self.db_manager = DatabaseManager()
        self.obsidian = ObsidianAdapter()
        
        # Базовый системный промпт будет дополняться в рантайме
        self.base_instructions = self._load_base_instructions()

    def _load_base_instructions(self) -> str:
        """Загружает базовые инструкции из GEMINI.md."""
        gemini_md_path = Path(__file__).parent.parent / "GEMINI.md"
        if gemini_md_path.exists():
            try:
                with open(gemini_md_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                print(f"Предупреждение: Не удалось прочитать GEMINI.md: {e}")
        return "Ты — NEXUS, главный ИИ-координатор команды агентов."

    def _get_current_system_prompt(self) -> str:
        """Формирует актуальный системный промпт с текущей датой и фактами из Layer 1."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Извлекаем приоритетные факты из Layer 1 (с учётом TTL и лимита)
        try:
            from agents.shared.python.db import get_active_facts
            from config import MEMORY_FACTS_LIMIT
            facts = get_active_facts(limit=MEMORY_FACTS_LIMIT)
        except Exception:
            facts = []
        
        facts_str = "\n".join(facts) if facts else "Нет сохраненных фактов."

        prompt = (
            f"ТЕКУЩЕЕ ВРЕМЯ СИСТЕМЫ: {now}\n"
            f"ВНИМАНИЕ: Все рынки на 2025 год и ранее считаются ИСТЕКШИМИ. Не анализируй их.\n\n"
            f"ТЫ — NEXUS, главный ИИ-координатор команды (SCOUT, SHADOW, HERALD).\n"
            f"Твоя цель — живой диалог, управление системой и глубокая аналитика.\n\n"
            f"ЯДРО ПАМЯТИ (Layer 1 - Durable Facts):\n{facts_str}\n\n"
            f"ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ:\n{self.base_instructions}"
        )
        return prompt

    # --- Функции-инструменты для работы с базой знаний (Obsidian) ---
    
    def read_obsidian_file(self, relative_path: str) -> str:
        """Читает содержимое конкретного файла из базы знаний (vault)."""
        content = self.obsidian.read_file(relative_path)
        if content is None:
            return f"Файл {relative_path} не найден в vault."
        return content

    def list_vault_files(self, sub_dir: str = "") -> str:
        """Возвращает список файлов в указанной директории базы знаний."""
        files = self.obsidian.list_files(sub_dir)
        if not files:
            return f"В директории '{sub_dir}' файлов не найдено."
        return "\n".join(files)

    def search_vault(self, query: str) -> str:
        """
        Ищет текст во всех файлах базы знаний.
        Сначала проверяет SQLite индекс (быстро), затем full-text grep (полно).
        Возвращает контекст вокруг совпадения, а не первые 200 символов.
        """
        results = []
        
        # 1. Быстрый поиск по SQLite индексу (заголовки, теги)
        try:
            from agents.shared.python.db import search_vault_index
            indexed = search_vault_index(query, limit=5)
            for item in indexed:
                results.append(f"📎 [{item.get('category', '?')}] {item.get('title', item['path'])}\n   Путь: {item['path']}")
        except Exception:
            pass
        
        # 2. Full-text поиск по файлам (с контекстом)
        vault_path = self.obsidian.vault_path
        for root, _, files in os.walk(vault_path):
            for file in files:
                if file.endswith(".md"):
                    full_path = Path(root) / file
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            lower_content = content.lower()
                            lower_query = query.lower()
                            pos = lower_content.find(lower_query)
                            if pos != -1:
                                rel_path = full_path.relative_to(vault_path)
                                # Извлекаем контекст: 100 символов до и 200 после совпадения
                                start = max(0, pos - 100)
                                end = min(len(content), pos + len(query) + 200)
                                snippet = content[start:end].strip()
                                if start > 0:
                                    snippet = "..." + snippet
                                if end < len(content):
                                    snippet = snippet + "..."
                                results.append(f"--- {rel_path} ---\n{snippet}")
                    except Exception:
                        continue
        
        if not results:
            return f"По запросу '{query}' ничего не найдено."
        return "\n\n".join(results[:10])

    def write_daily_summary(self, content: str) -> str:
        """Записывает ежедневный отчет (Daily Summary) в Obsidian."""
        path = self.obsidian.write_daily_summary(content)
        return f"Ежедневный отчет успешно сохранен: {path}"

    def promote_to_memory(self, category: str, filename: str, content: str) -> str:
        """Переносит важные инсайты в долгосрочную структурированную память."""
        try:
            path = self.obsidian.promote_to_memory(category, filename, content)
            return f"Информация перенесена в долгосрочную память: {path}"
        except Exception as e:
            return f"Ошибка при сохранении в память: {e}"

    # --- Инструменты для работы с БД (Layer 1) ---

    def save_memory_fact(self, key: str, value: Any) -> str:
        """Сохраняет важный факт в Layer 1 (Key-Value БД)."""
        try:
            self.db_manager.save_memory(key, value)
            return f"Факт '{key}' успешно сохранен в Layer 1."
        except Exception as e:
            return f"Ошибка при сохранении факта: {e}"

    def delete_memory_fact(self, key: str) -> str:
        """Удаляет факт из Layer 1."""
        try:
            with self.db_manager._get_connection() as conn:
                conn.execute("DELETE FROM memory WHERE key = ?", (key,))
                conn.commit()
            return f"Факт '{key}' удален из памяти."
        except Exception as e:
            return f"Ошибка при удалении: {e}"

    def delete_signal(self, signal_id: str) -> str:
        """Удаляет некорректный или устаревший сигнал из таблицы signals."""
        try:
            with self.db_manager._get_connection() as conn:
                conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
                conn.commit()
            return f"Сигнал {signal_id} успешно удален."
        except Exception as e:
            return f"Ошибка при удалении сигнала: {e}"

    def update_signal_status(self, signal_id: str, status: str) -> str:
        """Обновляет статус сигнала (например, на 'ARCHIVED' или 'EXECUTED')."""
        try:
            with self.db_manager._get_connection() as conn:
                conn.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
                conn.commit()
            return f"Статус сигнала {signal_id} изменен на {status}."
        except Exception as e:
            return f"Ошибка при обновлении статуса: {e}"

    def cleanup_expired_signals(self) -> str:
        """Помечает сигналы как EXECUTED, если время закрытия их рынков уже прошло."""
        now = datetime.utcnow().isoformat()
        try:
            with self.db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE signals 
                    SET status = 'EXECUTED' 
                    WHERE status = 'PENDING' AND market_id IN (
                        SELECT id FROM markets WHERE close_time < ?
                    )
                """, (now,))
                count = cursor.rowcount
                conn.commit()
            return f"Очистка завершена. Помечено как EXECUTED: {count} сигналов."
        except Exception as e:
            return f"Ошибка при очистке сигналов: {e}"

    def query_database(self, query: str) -> str:
        """Выполняет SELECT запрос к SQLite базе данных."""
        try:
            if not query.strip().upper().startswith("SELECT"):
                return "Ошибка: Допускаются только SELECT запросы. Используйте специальные инструменты для удаления или обновления."
                
            with self.db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                result = [dict(row) for row in rows]
                return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return f"Ошибка при запросе к БД: {e}"

    def _get_tools_declaration(self) -> List[Dict[str, Any]]:
        return [
            {
                "function_declarations": [
                    {
                        "name": "read_obsidian_file",
                        "description": "Читает содержимое файла из базы знаний (vault) по его пути",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "relative_path": {"type": "string", "description": "Путь к файлу относительно корня vault, например 'daily/2024-05-20-report.md'"}
                            },
                            "required": ["relative_path"]
                        }
                    },
                    {
                        "name": "list_vault_files",
                        "description": "Показывает список файлов в базе знаний.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "sub_dir": {"type": "string", "description": "Поддиректория (daily, memory, projects, inbox)"}
                            }
                        }
                    },
                    {
                        "name": "search_vault",
                        "description": "Ищет текст во всех заметках Obsidian. Используйте для поиска по ключевым словам.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "Текст для поиска"}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "save_memory_fact",
                        "description": "Сохраняет важную настройку или факт в Layer 1 (Key-Value БД). Помнится всегда.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "description": "Ключ (например, 'user_preference')"},
                                "value": {"type": "string", "description": "Значение (JSON или строка)"}
                            },
                            "required": ["key", "value"]
                        }
                    },
                    {
                        "name": "delete_memory_fact",
                        "description": "Удаляет факт из Layer 1.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "description": "Ключ для удаления"}
                            },
                            "required": ["key"]
                        }
                    },
                    {
                        "name": "delete_signal",
                        "description": "Удаляет сигнал из БД (например, если он ошибочный или за 2025 год).",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "signal_id": {"type": "string", "description": "ID сигнала из таблицы signals"}
                            },
                            "required": ["signal_id"]
                        }
                    },
                    {
                        "name": "update_signal_status",
                        "description": "Меняет статус сигнала в БД.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "signal_id": {"type": "string", "description": "ID сигнала"},
                                "status": {"type": "string", "description": "Новый статус (ARCHIVED, EXECUTED, PENDING)"}
                            },
                            "required": ["signal_id", "status"]
                        }
                    },
                    {
                        "name": "cleanup_expired_signals",
                        "description": "Автоматически помечает старые сигналы (например, за 2025 год) как исполненные.",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "write_daily_summary",
                        "description": "Записывает итоговый ежедневный отчет в Obsidian",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "Полный текст отчета в Markdown"}
                            },
                            "required": ["content"]
                        }
                    },
                    {
                        "name": "promote_to_memory",
                        "description": "Сохраняет важный паттерн или сущность в долгосрочную память Obsidian",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string", "enum": ["durable", "entities", "market-patterns", "source-profiles"]},
                                "filename": {"type": "string", "description": "Имя файла (без .md)"},
                                "content": {"type": "string", "description": "Содержимое заметки"}
                            },
                            "required": ["category", "filename", "content"]
                        }
                    },
                    {
                        "name": "query_database",
                        "description": "Выполняет поиск в SQL базе данных (только SELECT).",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "SQL SELECT запрос"}
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        ]

    def process_prompt(self, prompt: str, history: List[Dict[str, Any]] = None) -> str:
        """
        Основной цикл обработки запроса с поддержкой многошаговых вызовов функций.
        """
        # Динамически получаем модель из БД (если пользователь изменил ее через Telegram)
        selected_model = self.db_manager.get_memory("selected_model")
        current_model = selected_model if selected_model else self.model_name
        current_api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={self.api_key}"

        contents = history if history else []
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        # Получаем актуальный системный промпт (с текущей датой и фактами)
        current_system_prompt = self._get_current_system_prompt()

        payload = {
            "contents": contents,
            "tools": self._get_tools_declaration(),
            "systemInstruction": {"parts": [{"text": current_system_prompt}]},
            "tool_config": {"function_calling_config": {"mode": "AUTO"}}
        }

        max_iterations = 8
        for _ in range(max_iterations):
            try:
                response = requests.post(current_api_url, json=payload, timeout=60)
                if response.status_code != 200:
                    return f"Ошибка API: {response.status_code}\n{response.text}"

                res_json = response.json()
            except Exception as e:
                return f"Критическая ошибка при запросе к API: {e}"

            if 'candidates' not in res_json or not res_json['candidates']:
                return "Ошибка: Пустой ответ от API."

            candidate = res_json['candidates'][0]
            message = candidate.get('content', {})
            parts = message.get('parts', [])
            
            # Добавляем ответ модели в историю для следующего шага
            payload["contents"].append(message)

            function_calls = [p['functionCall'] for p in parts if 'functionCall' in p]
            
            if not function_calls:
                # Если вызовов функций нет, возвращаем текстовый ответ
                text_parts = [p['text'] for p in parts if 'text' in p]
                return "\n".join(text_parts) if text_parts else "Агент выполнил задачу."

            # Обрабатываем все вызовы функций в текущем ответе
            response_parts = []
            for call in function_calls:
                name = call['name']
                args = call['args']
                
                print(f"🔧 Nexus вызывает инструмент: {name}({args})")
                
                result = ""
                try:
                    if name == "read_obsidian_file":
                        result = self.read_obsidian_file(**args)
                    elif name == "list_vault_files":
                        result = self.list_vault_files(**args)
                    elif name == "search_vault":
                        result = self.search_vault(**args)
                    elif name == "write_daily_summary":
                        result = self.write_daily_summary(**args)
                    elif name == "promote_to_memory":
                        result = self.promote_to_memory(**args)
                    elif name == "save_memory_fact":
                        result = self.save_memory_fact(**args)
                    elif name == "delete_memory_fact":
                        result = self.delete_memory_fact(**args)
                    elif name == "delete_signal":
                        result = self.delete_signal(**args)
                    elif name == "update_signal_status":
                        result = self.update_signal_status(**args)
                    elif name == "cleanup_expired_signals":
                        result = self.cleanup_expired_signals()
                    elif name == "query_database":
                        result = self.query_database(**args)
                    elif name == "google_search":
                        result = "Поиск выполнен (через встроенный инструмент)."
                    else:
                        result = f"Ошибка: Инструмент {name} не реализован."
                except Exception as e:
                    result = f"Ошибка при выполнении {name}: {e}"

                response_parts.append({
                    "functionResponse": {
                        "name": name,
                        "response": {"result": result}
                    }
                })

            # Добавляем результаты функций в историю
            payload["contents"].append({
                "role": "function", 
                "parts": response_parts
            })
            
        return "Превышен лимит итераций вызова инструментов."

if __name__ == "__main__":
    # Быстрый тест
    if os.environ.get("GOOGLE_API_KEY"):
        agent = NexusAgent()
        print("Агент готов. Системный промпт загружен.")
