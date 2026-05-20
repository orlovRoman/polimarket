import sys
import os
from datetime import datetime, timedelta
import json
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from agents.orchestrator.src.agent import NexusAgent
from agents.shared.utils.database import DatabaseManager

def main():
    print("Начинаем генерацию Daily Summary...")
    db_manager = DatabaseManager()
    
    # Получаем данные за последние 24 часа
    yesterday = datetime.utcnow() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d %H:%M:%S")
    
    discussions = []
    signals = []
    
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM discussions WHERE timestamp >= ?", (date_str,))
            discussions = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT * FROM signals WHERE timestamp >= ?", (date_str,))
            signals = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Ошибка при чтении базы данных: {e}")
        return

    # Если данных нет, можно сгенерировать пустой отчет или отчет об отсутствии активности
    activity_data = {
        "discussions": discussions,
        "signals": signals,
        "period_start": date_str,
        "period_end": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    agent = NexusAgent()
    
    prompt = f"""
Ты — Nexus, главный Оркестратор системы.
Твоя задача — сгенерировать Daily Summary (ежедневный отчет) на основе следующих событий из базы данных за последние 24 часа:

Сырые данные (в формате JSON):
{json.dumps(activity_data, ensure_ascii=False, indent=2)}

Требования к формату отчета указаны в твоей системной инструкции (GEMINI.md) в разделе "Daily summary format".
Обязательно используй формат Markdown и структуру:
# Polimarket orchestrator daily — YYYY-MM-DD
## 1. High-priority opportunities
## 2. High-risk ambiguities
## 3. News-driven watch items
## 4. Strong-account signals
## 5. Decisions taken today
## 6. Open questions / TODO

Проанализируй данные, выдели главное и заполни соответствующие секции.
После генерации текста, ОБЯЗАТЕЛЬНО вызови инструмент write_daily_summary, чтобы сохранить отчет в базу знаний Obsidian (vault).
Твой текстовый ответ может быть кратким подтверждением, главное — вызвать инструмент записи.
"""
    
    print("Отправляем запрос к NexusAgent...")
    try:
        response = agent.process_prompt(prompt)
        print("Ответ NexusAgent:")
        print(response)
        print("Генерация завершена.")
    except Exception as e:
        print(f"Ошибка при работе NexusAgent: {e}")

if __name__ == "__main__":
    main()
