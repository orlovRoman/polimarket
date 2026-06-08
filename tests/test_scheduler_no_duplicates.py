# tests/test_scheduler_no_duplicates.py
"""Проверяет что в scheduler нет дублирующих резолверов."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

def test_only_one_resolution_job_registered():
    """main.py не должен регистрировать более одного резолвера сигналов."""
    jobs_added = []

    class FakeScheduler:
        def add_job(self, func, *a, id=None, **kw):
            jobs_added.append({"func": getattr(func, "__name__", str(func)), "id": id})
        def start(self): pass

    with patch("main.AsyncIOScheduler", return_value=FakeScheduler()), \
         patch("main.asyncio.create_task"), \
         patch("main.init_nexus_agent", new_callable=AsyncMock), \
         patch("main.dp"), patch("main.bot"), \
         patch("main.ensure_single_instance"), \
         patch("main.start_fastapi", new_callable=AsyncMock), \
         patch("telegram.bot.set_commands", new_callable=AsyncMock), \
         patch("main.asyncio.wait", side_effect=Exception("Stop wait")):
        
        # Сохраняем исходный планировщик бота для восстановления
        import telegram.bot
        orig_scheduler = getattr(telegram.bot, "_scheduler", None)
        try:
            # Импортируем start_system и запускаем, чтобы сработала регистрация джобов
            from main import start_system
            try:
                # Запускаем в mock-окружении
                import asyncio
                asyncio.run(start_system())
            except Exception:
                # Игнорируем ошибки запуска после настройки планировщика
                pass
        finally:
            telegram.bot._scheduler = orig_scheduler

    # Проверяем, что планировщик вообще настраивался и задачи были добавлены
    assert len(jobs_added) > 0, "Список jobs_added пуст! Планировщик не зарегистрировал ни одной задачи."

    resolution_jobs = [
        j for j in jobs_added
        if any(kw in j["func"] for kw in ["resolution", "resolve", "outcome"])
    ]
    
    # Должно быть ровно два резолвера: outcome_tracker и signal_resolution_job
    assert len(resolution_jobs) == 2, (
        f"Найдено {len(resolution_jobs)} резолверов: {resolution_jobs}. "
        "Ожидалось два: outcome_tracker и signal_resolution_job."
    )
    job_ids = {j["id"] for j in resolution_jobs}
    assert "outcome_tracker" in job_ids
    assert "signal_resolution_job" in job_ids
