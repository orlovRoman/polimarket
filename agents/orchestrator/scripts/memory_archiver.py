import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone

# Добавляем корень проекта в sys.path
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from agents.orchestrator.src.agent import NexusAgent
from agents.shared.utils.database import DatabaseManager
from agents.shared.python.db import cleanup_stale_signals
from config import DB_PATH
from agents.orchestrator.scripts.infra_config import INFRA_CONFIG

def backup_database():
    """Создаёт бэкап БД перед опасными операциями (GC, миграции)."""
    import shutil
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"database_{timestamp}.sqlite"
    shutil.copy2(str(DB_PATH), str(backup_path))
    # Удаляем бэкапы старше (оставляем до backups_keep_max штук)
    backups = sorted(backup_dir.glob("database_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[INFRA_CONFIG.backups_keep_max:]:
        old.unlink(missing_ok=True)
    return backup_path

def main():
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    
    if dry_run:
        print("ВНИМАНИЕ: Запуск в режиме DRY-RUN. Данные не будут удалены или изменены.")
        
    print("Запуск сборки мусора и архивации памяти (Memory GC)...")
    
    # Бэкап БД перед GC
    if not dry_run:
        try:
            bp = backup_database()
            print(f"Бэкап БД: {bp}")
        except Exception as e:
            print(f"Предупреждение: бэкап не удался ({e}), продолжаем...")
    
    db_manager = DatabaseManager()
    agent = NexusAgent()
    
    # 0. Автоочистка устаревших сигналов (2025, истёкшие рынки)
    stale = cleanup_stale_signals()
    if stale > 0:
        print(f"Автоочистка: архивировано {stale} устаревших сигналов.")

    # 1. Сначала пометим как EXECUTED те сигналы, чьи рынки уже закрылись (события 2025 года и т.д.)
    now = datetime.now(timezone.utc).isoformat()
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
            if not dry_run:
                conn.commit()
    except Exception as e:
        print(f"Ошибка при обновлении статуса просроченных сигналов: {e}")

    # 2. Ищем сигналы со статусом EXECUTED или EVALUATED для архивации
    executed_signals = []
    try:
        with db_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM signals WHERE status IN ('EXECUTED', 'EVALUATED')")
            executed_signals = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Ошибка при чтении сигналов: {e}")
        return

    if not executed_signals:
        print("Нет исполненных (EXECUTED/EVALUATED) сигналов для архивации.")
        return

    print(f"Найдено сигналов для архивации: {len(executed_signals)}")

    for signal in executed_signals:
        market_id = signal['market_id']
        signal_id = signal['id']
        print(f"Обработка маркета {market_id} (сигнал {signal_id})...")
        
        # Получаем все обсуждения по этому маркету из agent_opinions
        discussions = []
        evaluations = []
        try:
            with db_manager._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM agent_opinions WHERE market_id = ?", (market_id,))
                discussions = [dict(row) for row in cursor.fetchall()]
                
                # Fetch ground truth evaluations
                cursor.execute("SELECT summary, agent_name, outcome FROM agent_episodes WHERE market_id = ? AND event_type = 'signal_evaluated'", (market_id,))
                episodes = cursor.fetchall()
                evaluations = [row['summary'] for row in episodes]
                
                agent_outcomes = []
                for row in episodes:
                    if row['agent_name'] and row['outcome']:
                        agent_outcomes.append(f"{row['agent_name']}: {row['outcome']}")
                        
        except Exception as e:
            print(f"Ошибка при получении данных для {market_id}: {e}")
            continue

        prompt = f"""
Ты — Nexus, главный Оркестратор. 
Сделка (сигнал) по маркету {market_id} была оценена или срок её действия истёк. 
Твоя задача — проанализировать сырые данные и сгенерировать "постмортем" (post-mortem) заметку.
Объясни, почему идея сработала (или не сработала), какие паттерны были замечены и какой урок можно извлечь на будущее.

Данные сигнала:
{json.dumps(signal, ensure_ascii=False, indent=2)}

История обсуждений агентов:
{json.dumps(discussions, ensure_ascii=False, indent=2)}

Результаты реальной проверки (Ground Truth):
{chr(10).join(evaluations) if evaluations else "Рынок закрылся без явной оценки."}

Сгенерируй полезную заметку в формате Markdown и вызови инструмент promote_to_memory, чтобы сохранить ее:
- category: "durable" (или "market-patterns", если это явный рыночный паттерн).
- filename: "{market_id}-postmortem.md" (или добавь к существующему тематическому файлу, например "crypto-regulation.md", если это относится к общей теме).
- content: <твой текст заметки>

Твой ответ может быть кратким подтверждением, главное — вызвать инструмент записи.
"""
        
        try:
            print(f"Отправка запроса агенту для маркета {market_id}...")
            if not dry_run:
                response = agent.process_prompt(prompt)
                print(f"Ответ агента: {response}")
                # Пытаемся найти путь файла в ответе агента
                import re
                path_match = re.search(r'vault/memory/.*\w+\.md', response)
                if path_match:
                    print(f"Post-mortem сохранен по пути: {path_match.group(0)}")
                
                if agent_outcomes:
                    print(f"Исходы работы агентов по рынку {market_id}: {', '.join(agent_outcomes)}")
                
                # После успешного анализа удаляем сырые данные из базы
                with db_manager._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM agent_opinions WHERE market_id = ?", (market_id,))
                    # Меняем статус сигнала на ARCHIVED, чтобы не обрабатывать повторно
                    cursor.execute("UPDATE signals SET status = 'ARCHIVED' WHERE id = ?", (signal_id,))
                    conn.commit()
                print(f"Очистка завершена: сырые обсуждения по маркету {market_id} удалены, сигнал архивирован.")
            else:
                print(f"[DRY-RUN] Запрос к LLM пропущен. Исходы агентов: {', '.join(agent_outcomes)}")
                print(f"[DRY-RUN] Очистка таблиц пропущена.")
            
        except Exception as e:
            print(f"Ошибка при обработке {market_id}: {e}")

    print("Архивация завершена.")

if __name__ == "__main__":
    main()
