import sys
import os
import json
from pathlib import Path
from datetime import datetime

# Добавляем корень проекта в sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from agents.orchestrator.src.agent import NexusAgent
from agents.shared.utils.database import DatabaseManager

def main():
    print("Запуск сборки мусора и архивации памяти (Memory GC)...")
    db_manager = DatabaseManager()
    agent = NexusAgent()
    
    # 1. Сначала пометим как EXECUTED те сигналы, чьи рынки уже закрылись (события 2025 года и т.д.)
    now = datetime.utcnow().isoformat()
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            # Находим сигналы для рынков, которые закрылись раньше текущего момента
            cursor.execute("""
                UPDATE signals 
                SET status = 'EXECUTED' 
                WHERE status = 'PENDING' AND market_id IN (
                    SELECT id FROM markets WHERE close_time < ?
                )
            """, (now,))
            if cursor.rowcount > 0:
                print(f"Помечено как EXECUTED из-за истечения времени: {cursor.rowcount} сигналов.")
            conn.commit()
    except Exception as e:
        print(f"Ошибка при обновлении статуса просроченных сигналов: {e}")

    # 2. Ищем сигналы со статусом EXECUTED для архивации
    executed_signals = []
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals WHERE status = 'EXECUTED'")
            executed_signals = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Ошибка при чтении сигналов: {e}")
        return

    if not executed_signals:
        print("Нет исполненных (EXECUTED) сигналов для архивации.")
        return

    print(f"Найдено сигналов для архивации: {len(executed_signals)}")

    for signal in executed_signals:
        market_id = signal['market_id']
        signal_id = signal['id']
        print(f"Обработка маркета {market_id} (сигнал {signal_id})...")
        
        # Получаем все обсуждения по этому маркету из agent_opinions
        discussions = []
        try:
            with db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM agent_opinions WHERE market_id = ?", (market_id,))
                discussions = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Ошибка при получении обсуждений для {market_id}: {e}")
            continue

        prompt = f"""
Ты — Nexus, главный Оркестратор. 
Сделка (сигнал) по маркету {market_id} была исполнена или срок её действия истёк. 
Твоя задача — проанализировать сырые данные и сгенерировать "постмортем" (post-mortem) заметку, объясняющую, почему идея сработала (или не сработала), какие паттерны были замечены и какой урок можно извлечь на будущее.

Данные сигнала:
{json.dumps(signal, ensure_ascii=False, indent=2)}

История обсуждений агентов:
{json.dumps(discussions, ensure_ascii=False, indent=2)}

Сгенерируй полезную заметку в формате Markdown и вызови инструмент promote_to_memory, чтобы сохранить ее:
- category: "durable" (или "market-patterns", если это явный рыночный паттерн).
- filename: "{market_id}-postmortem.md"
- content: <твой текст заметки>

Твой ответ может быть кратким подтверждением, главное — вызвать инструмент записи.
"""
        
        try:
            print(f"Отправка запроса агенту для маркета {market_id}...")
            response = agent.process_prompt(prompt)
            print(f"Ответ агента: {response}")
            
            # После успешного анализа удаляем сырые данные из базы
            with db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM agent_opinions WHERE market_id = ?", (market_id,))
                # Меняем статус сигнала на ARCHIVED, чтобы не обрабатывать повторно
                cursor.execute("UPDATE signals SET status = 'ARCHIVED' WHERE id = ?", (signal_id,))
                conn.commit()
            print(f"Очистка завершена: сырые обсуждения по маркету {market_id} удалены, сигнал архивирован.")
            
        except Exception as e:
            print(f"Ошибка при обработке {market_id}: {e}")

    print("Архивация завершена.")

if __name__ == "__main__":
    main()
