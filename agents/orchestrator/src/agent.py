import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from google import genai
from google.genai import types

from agents.shared.utils.database import DatabaseManager
from agents.shared.utils.obsidian_adapter import ObsidianAdapter

class NexusAgent:
    """
    Оркестратор (NexusAgent), использующий Gemini API (через Google GenAI SDK).
    Отвечает за координацию, генерацию отчетов и взаимодействие с памятью (SQLite и Obsidian).
    """

    def __init__(self, model_name: str = "gemini-2.5-pro", api_key: Optional[str] = None):
        self.model_name = model_name
        # Используем api_key, если передан, иначе берем из переменных окружения
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client()

        self.db_manager = DatabaseManager()
        self.obsidian = ObsidianAdapter()

    # --- Tool Functions ---
    def read_obsidian_file(self, relative_path: str) -> str:
        """
        Читает содержимое файла из базы знаний (vault) по относительному пути (например, 'daily/2026-05-20-polimarket-orchestrator.md').
        """
        content = self.obsidian.read_file(relative_path)
        if content is None:
            return f"Файл {relative_path} не найден."
        return content

    def write_daily_summary(self, content: str) -> str:
        """
        Записывает ежедневный отчет (Daily Summary) в Obsidian.
        """
        path = self.obsidian.write_daily_summary(content)
        return f"Успешно сохранен отчет по пути: {path}"

    def write_to_inbox(self, filename: str, content: str) -> str:
        """
        Сохраняет сырые данные/идеи в папку inbox Obsidian.
        """
        path = self.obsidian.write_to_inbox(filename, content)
        return f"Успешно сохранено в inbox по пути: {path}"

    def promote_to_memory(self, category: str, filename: str, content: str) -> str:
        """
        Сохраняет долгосрочную память (Layer 3). Допустимые категории: 'durable', 'entities', 'market-patterns', 'source-profiles'.
        """
        try:
            path = self.obsidian.promote_to_memory(category, filename, content)
            return f"Успешно сохранено в память по пути: {path}"
        except Exception as e:
            return f"Ошибка при сохранении: {e}"

    def append_to_project_notes(self, filename: str, content: str) -> str:
        """
        Добавляет записи в проекты/стратегии в Obsidian.
        """
        path = self.obsidian.append_to_project_notes(filename, content)
        return f"Успешно добавлено в проектные заметки: {path}"

    def query_database(self, query: str, params: tuple = ()) -> str:
        """
        Выполняет SELECT запрос к SQLite базе данных (таблицы: wallets, discussions, signals). Возвращает JSON-строку.
        Не использовать для разрушающих запросов (INSERT, UPDATE, DELETE).
        """
        try:
            with self.db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                result = [dict(row) for row in rows]
                return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return f"Ошибка базы данных: {e}"

    # --- Agent Execution ---
    def process_prompt(self, prompt: str) -> str:
        """
        Обрабатывает промпт с использованием Gemini API и доступных инструментов.
        """
        tools = [
            self.read_obsidian_file,
            self.write_daily_summary,
            self.write_to_inbox,
            self.promote_to_memory,
            self.append_to_project_notes,
            self.query_database
        ]

        config = types.GenerateContentConfig(
            tools=tools,
            temperature=0.2,
        )

        chat = self.client.chats.create(model=self.model_name, config=config)
        response = chat.send_message(prompt)
        
        return response.text

if __name__ == "__main__":
    # Быстрый тест (если задан GEMINI_API_KEY)
    if os.environ.get("GEMINI_API_KEY"):
        agent = NexusAgent()
        print("Агент инициализирован. Можно передавать запросы.")
