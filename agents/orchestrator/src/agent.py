import os
import json
import requests
from datetime import datetime
from typing import Optional, List, Dict, Any

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
        
        :param model_name: Версия модели Gemini для использования
        :param api_key: API ключ (загружается из окружения, если не передан)
        """
        self.model_name = model_name
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Критическая ошибка: GOOGLE_API_KEY не найден")
        
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
        self.db_manager = DatabaseManager()
        self.obsidian = ObsidianAdapter()

    # --- Функции-инструменты для работы с базой знаний (Obsidian) ---
    
    def read_obsidian_file(self, relative_path: str) -> str:
        """
        Читает содержимое конкретного файла из базы знаний (vault).
        Позволяет агенту извлекать исторический контекст.
        """
        content = self.obsidian.read_file(relative_path)
        if content is None:
            return f"Файл {relative_path} не найден в vault."
        return content

    def write_daily_summary(self, content: str) -> str:
        """
        Формирует и записывает ежедневный отчет (Daily Summary) в Obsidian.
        Используется для подведения итогов дня и планирования.
        """
        path = self.obsidian.write_daily_summary(content)
        return f"Ежедневный отчет успешно сохранен: {path}"

    def write_to_inbox(self, filename: str, content: str) -> str:
        """
        Сохраняет сырые данные, идеи или входящие сообщения в папку inbox.
        Для последующей ручной или автоматической обработки.
        """
        path = self.obsidian.write_to_inbox(filename, content)
        return f"Данные сохранены в inbox: {path}"

    def promote_to_memory(self, category: str, filename: str, content: str) -> str:
        """
        Переносит важные инсайты в долгосрочную структурированную память (Layer 3).
        """
        try:
            path = self.obsidian.promote_to_memory(category, filename, content)
            return f"Информация перенесена в долгосрочную память: {path}"
        except Exception as e:
            return f"Ошибка при сохранении в память: {e}"

    def append_to_project_notes(self, filename: str, content: str) -> str:
        """
        Добавляет новые заметки или стратегии в проектные файлы.
        """
        path = self.obsidian.append_to_project_notes(filename, content)
        return f"Добавлено в проектные заметки: {path}"

    def query_database(self, query: str, params: tuple = ()) -> str:
        """
        Выполняет прямой SQL-запрос к локальной базе данных SQLite.
        Используется для извлечения статистики или поиска конкретных сигналов.
        """
        try:
            with self.db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                result = [dict(row) for row in rows]
                return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return f"Ошибка при запросе к БД: {e}"

    # --- Agent Execution ---
    def process_prompt(self, prompt: str) -> str:
        """Обрабатывает промпт с использованием Gemini API и инструментов."""
        
        tools = [
            {
                "google_search": {}
            },
            {
                "function_declarations": [
                    {
                        "name": "read_obsidian_file",
                        "description": "Читает содержимое файла из базы знаний (vault)",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "relative_path": {"type": "string"}
                            },
                            "required": ["relative_path"]
                        }
                    },
                    {
                        "name": "write_daily_summary",
                        "description": "Записывает ежедневный отчет в Obsidian",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"}
                            },
                            "required": ["content"]
                        }
                    },
                    {
                        "name": "promote_to_memory",
                        "description": "Сохраняет долгосрочную память (Layer 3)",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "category": {"type": "string"},
                                "filename": {"type": "string"},
                                "content": {"type": "string"}
                            },
                            "required": ["category", "filename", "content"]
                        }
                    },
                    {
                        "name": "query_database",
                        "description": "Выполняет SELECT запрос к SQLite базе данных (wallets, agent_opinions, signals)",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"}
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        ]

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": tools,
            "tool_config": {"function_calling_config": {"mode": "AUTO"}}
        }

        response = requests.post(self.api_url, json=payload, timeout=60)
        if response.status_code != 200:
            return f"Ошибка API: {response.text}"

        res_json = response.json()
        
        # Обработка вызовов инструментов (упрощенно)
        part = res_json['candidates'][0]['content']['parts'][0]
        if 'functionCall' in part:
            call = part['functionCall']
            name = call['name']
            args = call['args']
            
            print(f"Вызов инструмента: {name}({args})")
            
            result = ""
            if name == "read_obsidian_file":
                result = self.read_obsidian_file(**args)
            elif name == "write_daily_summary":
                result = self.write_daily_summary(**args)
            elif name == "promote_to_memory":
                result = self.promote_to_memory(**args)
            elif name == "query_database":
                result = self.query_database(**args)
            
            # Вторая итерация: передаем результат инструмента обратно
            payload["contents"].append(res_json['candidates'][0]['content'])
            payload["contents"].append({
                "role": "model",
                "parts": [{
                    "functionResponse": {
                        "name": name,
                        "response": {"result": result}
                    }
                }]
            })
            
            response = requests.post(self.api_url, json=payload, timeout=60)
            if response.status_code != 200:
                return f"Ошибка API (инструмент): {response.text}"
            
            res_json = response.json()
            return res_json['candidates'][0]['content']['parts'][0]['text']

        return part['text']

if __name__ == "__main__":
    if os.environ.get("GOOGLE_API_KEY"):
        agent = NexusAgent()
        print("Агент инициализирован.")
