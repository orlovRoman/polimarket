import asyncio
import logging
logger = logging.getLogger("NexusPolyBot")
import os
import sqlite3
import subprocess
import requests
import json
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand, LinkPreviewOptions
from aiogram.exceptions import TelegramRetryAfter
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timedelta

# Импортируем функции БД
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.shared.python.db import (
    save_chat_message, get_chat_history, init_db, get_db_stats, get_signals,
    cleanup_chat_history, cleanup_stale_signals,
    add_to_market_list, remove_from_market_list, is_in_market_list,
    get_market_list, is_alert_already_sent
)
from agents.orchestrator.src.agent import NexusAgent

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
LOG_PATH = Path(__file__).parent.parent / "logs" / "main.log"

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env файле!")

# Инициализируем БД при старте
init_db()

# --- Option A+: NexusAgent не создаётся при импорте ---
# Инициализация происходит явно через init_nexus_agent() в start_system() (main.py).
# Это исключает тяжёлую синхронную работу (загрузка промпта, инициализация Gemini)
# внутри импорта и при конкурентных запросах нет нескольких экземпляров агента.
_nexus_agent: NexusAgent | None = None
def get_core_engine():
    """Возвращает единственный экземпляр CoreEngine."""
    from core.engine import CoreEngine
    return CoreEngine()


def get_nexus_agent() -> NexusAgent:
    """Возвращает единственный экземпляр NexusAgent. Если он ещё не инициализирован,
    поднимает RuntimeError (не должно происходить после корректного старта)."""
    if _nexus_agent is None:
        raise RuntimeError(
            "NexusAgent не инициализирован. "
            "Убедитесь, что await init_nexus_agent() вызван в start_system() до polling."
        )
    return _nexus_agent


async def init_nexus_agent() -> None:
    """Асинхронная инициализация NexusAgent при старте системы.
    Вызывается один раз из start_system() в main.py."""
    global _nexus_agent
    if _nexus_agent is not None:
        return  # Уже инициализирован — ничего не делаем
    import logging
    log = logging.getLogger("NexusPolyBot")
    log.info("Инициализация NexusAgent...")
    # Инициализируем в отдельном потоке, чтобы не блокировать event loop
    _nexus_agent = await asyncio.to_thread(NexusAgent)
    log.info("✅ NexusAgent инициализирован успешно.")

# Инициализируем бота и диспетчер событий aiogram
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ID чата, авторизованного для управления ботом
AUTHORIZED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if not AUTHORIZED_CHAT_ID:
    raise EnvironmentError(
        "CRITICAL: TELEGRAM_CHAT_ID не задан в .env!\n"
        "Без него бот доступен ЛЮБОМУ пользователю Telegram.\n"
        "Добавьте строку: TELEGRAM_CHAT_ID=<ваш_chat_id>"
    )

async def set_commands(bot: Bot):
    """
    Настраивает меню команд в интерфейсе Telegram-бота.
    """
    commands = [
        BotCommand(command="start", description="Начало работы"),
        BotCommand(command="help", description="Справка по командам"),
        BotCommand(command="status", description="Проверка статуса системы"),
        BotCommand(command="scan", description="Запуск анализа рынков"),
        BotCommand(command="ideas", description="Просмотр найденных идей"),
        BotCommand(command="history", description="Архив закрытых рынков"),
        BotCommand(command="correlations", description="Корреляции между рынками"),
        BotCommand(command="performance", description="Точность агентов и история прогнозов"),
        BotCommand(command="stats", description="Статистика базы данных"),
        BotCommand(command="settings", description="Настройка лимитов запросов"),
        BotCommand(command="model", description="Выбор языковой модели"),
        BotCommand(command="logs", description="Просмотр последних логов"),
        BotCommand(command="cleanup", description="Очистить устаревшие сигналы"),
        BotCommand(command="restart", description="Перезапуск бота"),
        BotCommand(command="health", description="Здоровье системы (LLM и чекпоинты)"),
        BotCommand(command="arbitrage", description="Запуск кросс-платформенного арбитража (PM ↔ Kalshi)"),
        BotCommand(command="corridor", description="Временной арбитраж (Temporal Corridor)"),
        BotCommand(command="lists", description="Списки рынков: Игнорировать / Следить"),
    ]
    await bot.set_my_commands(commands)

# Глобальный лок для предотвращения одновременных запусков сканирования
_scan_lock = asyncio.Lock()

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Stale check for messages
        if isinstance(event, types.Message):
            if event.date:
                try:
                    from datetime import datetime, timedelta
                    now = datetime.now(event.date.tzinfo)
                    if (now - event.date) > timedelta(seconds=30):
                        return
                except Exception:
                    pass
                    
        # Auth check
        user_id = str(event.from_user.id) if hasattr(event, "from_user") and event.from_user else None
        chat_id = str(event.chat.id) if hasattr(event, "chat") and event.chat else None
        if not chat_id and hasattr(event, "message") and event.message:
            chat_id = str(event.message.chat.id)
        
        allowed = False
        if AUTHORIZED_CHAT_ID:
            if chat_id and chat_id == AUTHORIZED_CHAT_ID:
                allowed = True
            elif user_id and user_id == AUTHORIZED_CHAT_ID:
                allowed = True
                
        if not allowed:
            # Предупреждаем неавторизованного пользователя
            if isinstance(event, types.Message):
                try:
                    await event.answer("⛔ <b>Доступ заблокирован.</b>\nВаш Telegram ID не авторизован в настройках бота.")
                except Exception:
                    pass
            return

        return await handler(event, data)

dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())


def ask_gemini(text: str, history: list = None) -> str:
    """
    Отправляет запрос к Gemini API через NexusAgent для получения ответа от NEXUS.
    
    :param text: Сообщение пользователя
    :param history: История диалога для сохранения контекста
    :return: Ответ ИИ или сообщение об ошибке
    """
    try:
        # NexusAgent.process_prompt уже содержит логику системных инструкций и инструментов
        return get_nexus_agent().process_prompt(text, history)
    except Exception as e:
        return f"Ошибка при обращении к NEXUS: {e}"

def estimate_llm_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """
    Рассчитывает ориентировочную стоимость вызова LLM в USD на основе количества токенов.
    """
    model = model_name.lower().strip()
    
    # 1. Бесплатные модели (содержат free или :free)
    if "free" in model:
        return 0.0
        
    # 2. Тарифы Gemini Flash (2.5 / 2.0)
    if "gemini-2.5-flash" in model or "gemini-2.0-flash" in model or "flash" in model:
        # $0.075 / 1M input, $0.30 / 1M output
        return (input_tokens * 0.075 / 1_000_000) + (output_tokens * 0.30 / 1_000_000)
        
    # 3. Тарифы Gemini Pro (2.5 / 1.5)
    if "gemini-2.5-pro" in model or "gemini-1.5-pro" in model or ("gemini" in model and "pro" in model):
        # $1.25 / 1M input, $5.00 / 1M output
        return (input_tokens * 1.25 / 1_000_000) + (output_tokens * 5.00 / 1_000_000)
        
    # 4. Fallback по умолчанию для прочих платных моделей (OpenRouter, etc.)
    # $0.50 / 1M input, $1.50 / 1M output
    return (input_tokens * 0.50 / 1_000_000) + (output_tokens * 1.50 / 1_000_000)

def build_paginated_keyboard(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с пагинацией."""
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}_{page - 1}"))
    nav_row.append(InlineKeyboardButton(text="❌ Закрыть", callback_data="close_message"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{prefix}_{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav_row])


def build_market_action_keyboard(market_id: str, market_title: str) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопками 'Игнорировать' и 'Следить'.
    market_id обрезается до 40 символов для соблюдения лимита callback_data = 64 байта aiogram.
    """
    mid = market_id[:40]  # UUID = 36 символов, вписывается с префиксами 11 симв (ignore_mkt_ / watch_mkt_)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Игнорировать", callback_data=f"ignore_mkt_{mid}"),
            InlineKeyboardButton(text="👁 Следить", callback_data=f"watch_mkt_{mid}"),
        ]
    ])


async def send_or_edit(message_or_callback, text: str, keyboard: InlineKeyboardMarkup = None) -> None:
    """Вспомогательная функция для отправки нового или редактирования существующего сообщения."""
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    else:
        await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        await message_or_callback.answer()

@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    """
    Обработчик команды /start. Приветствует пользователя.
    """
    welcome_text = (
        f"Привет, <b>{message.from_user.full_name}</b>! 👋\n\n"
        f"Я <b>NEXUS</b> — терминал управления AI-командой Polymarket.\n\n"
        f"Моя задача — непрерывный мониторинг рынков и поиск возможностей.\n\n"
        f"Используй /help, чтобы увидеть, что я умею."
    )
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def command_help_handler(message: types.Message) -> None:
    help_text = (
        "📚 <b>Справочник команд NEXUS:</b>\n\n"
        "<b>Основные:</b>\n"
        "🚀 /scan — запустить поиск идей (выбор из 7 категорий)\n"
        "💡 /ideas — показать последние 5 активных сигналов\n"
        "⚙️ /status — детальный статус агентов и метрики (в т.ч. Точность SCOUT*)\n"
        "📊 /audit — аудит воронки идей (отказы SHADOW)\n\n"
        "<b>Настройки:</b>\n"
        "🛠 /settings — лимит рынков + порог Edge (SCOUT)\n"
        "🧠 /model — выбрать языковую модель Gemini\n"
        "📈 /stats — общая статистика (рынки, сигналы)\n"
        "🧹 /cleanup — архивировать устаревшие сигналы\n"
        "📜 /logs — последние 10 строк системного лога\n\n"
        "❓ /help — это сообщение\n"
        "👋 /start — перезапустить приветствие\n\n"
        "<b>Экспериментальные функции:</b>\n"
        "⚖️ /arbitrage — кросс-платформенный арбитраж (Polymarket ↔ Kalshi)\n"
        "🔄 /synthetic — внутрирыночный арбитраж (синтетические коридоры Polymarket)\n\n"
        "<i>*Точность SCOUT в меню /status показывает % успешных сигналов. Она 'накапливается', пока рынки, по которым бот дал сигнал, физически не закроются на Polymarket, чтобы сверить прогноз с реальностью.</i>\n\n"
        "<i>Ты также можешь просто писать мне вопросы в чат — я отвечу, используя контекст нашей команды.</i>"
    )
    await message.answer(help_text)

@dp.message(Command("synthetic"))
async def command_synthetic_handler(message: types.Message) -> None:
    """Запуск сканирования синтетических коридоров по запросу."""
    await message.answer("🔄 Запускаю математический поиск синтетических коридоров (Polymarket). Это займет пару минут...")
    try:
        from services.synthetic_corridor_scanner import run_synthetic_corridor_scan
        from services.notifications import send_synthetic_corridor_alerts
        import asyncio
        
        found = await asyncio.to_thread(
            run_synthetic_corridor_scan,
            poly_limit=200,  # Чуть больше лимит при ручном скане
            budget_per_trade=200.0,
        )
        if found:
            await asyncio.to_thread(send_synthetic_corridor_alerts)
            await message.answer(f"✅ Сканирование завершено. Найдено {len(found)} коридоров. Алерты отправлены.")
        else:
            await message.answer("🤷‍♂️ Сканирование завершено. Синтетические коридоры (спред > 1.5%) не найдены.")
    except Exception as e:
        logger.error(f"Ошибка ручного сканирования синтетических коридоров: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при сканировании: {e}")

@dp.message(Command("health"))
async def command_health_handler(message: types.Message) -> None:
    from config import llm_health_gate
    from core.checkpoint import _checkpoints_cache
    from datetime import datetime
    
    # Сбор данных LLMHealthGate
    state = llm_health_gate.state
    state_emoji = "🟢" if state == "HEALTHY" else "🟡" if state == "DEGRADED" else "🔴"
    
    now = datetime.now()  # LLMHealthGate.error_timestamps тоже naive (datetime.now()), согласовано
    error_cnt = len([t for t in llm_health_gate.error_timestamps if (now - t).total_seconds() <= 60])

    backoff_active = "ДА" if llm_health_gate.retry_after > now else "НЕТ"
    last_429 = "Нет данных"
    if llm_health_gate.error_timestamps:
        last_time = llm_health_gate.error_timestamps[-1]
        mins_ago = int((now - last_time).total_seconds() / 60)
        last_429 = f"{last_time.strftime('%H:%M:%S')} ({mins_ago} мин назад)"
        
    lock_status = "🔴 занят" if _scan_lock.locked() else "🟢 свободен"
    
    lines = [
        "🏥 <b>Здоровье системы:</b>",
        f"● Gemini API: {state_emoji} {state} ({error_cnt} ошибок за 1 мин)",
        f"● Последний 429: {last_429}",
        f"● Backoff активен: {backoff_active}",
    ]
    
    # Собираем чекпоинты (последние для каждого агента)
    # Ищем самые свежие чекпоинты, начинающиеся с scout_, swing_, shadow_
    def get_latest_cp(prefix: str):
        cps = [(k, v) for k, v in _checkpoints_cache.items() if k.startswith(prefix)]
        if not cps: return "Нет данных"
        cps.sort(key=lambda x: x[1].get('timestamp', ''), reverse=True)
        latest = cps[0][1]
        st = latest.get("status", "unknown")
        emoji = "✅" if st == "ok" else "⚠️" if st == "timeout" else "❌"
        ts = latest.get('timestamp', '')
        time_str = ts.split("T")[1][:8] if "T" in ts else ts
        return f"{emoji} {st} ({time_str})"
        
    lines.append(f"● Последний checkpoint SCOUT: {get_latest_cp('scout_')}")
    lines.append(f"● Последний checkpoint SWING: {get_latest_cp('swing_')}")
    lines.append(f"● Последний checkpoint SHADOW: {get_latest_cp('shadow_')}")
    lines.append(f"● Lock статус: {lock_status}")
    
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("status"))
async def command_status_handler(message: types.Message) -> None:
    from agents.shared.python.db import DB_PATH, get_connection, get_memory_stats, get_memory
    
    # Получаем настройки и метрики из БД
    last_scan_str = "Неизвестно"
    scan_limit = 10
    trend_hunter_enabled = True
    trend_hunter_alerts = True
    trend_hunter_last_run = "Никогда"
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM memory WHERE key IN ('last_scan_time', 'scan_limit', 'trend_hunter_enabled', 'trend_hunter_alerts_enabled', 'trend_hunter_last_run')")
            rows = cursor.fetchall()
            for row in rows:
                if row['key'] == 'last_scan_time':
                    last_scan_str = json.loads(row['value'])
                elif row['key'] == 'scan_limit':
                    scan_limit = json.loads(row['value'])
                elif row['key'] == 'trend_hunter_enabled':
                    trend_hunter_enabled = json.loads(row['value'])
                elif row['key'] == 'trend_hunter_alerts_enabled':
                    trend_hunter_alerts = json.loads(row['value'])
                elif row['key'] == 'trend_hunter_last_run':
                    trend_hunter_last_run = json.loads(row['value'])
    except Exception:
        pass

    # Получаем метрики памяти
    stats = await asyncio.to_thread(get_memory_stats)

    engine = get_core_engine()
    is_scanning_real = _scan_lock.locked() or engine._scan_lock.locked()

    status_text = (
        "📊 <b>Статус системы (24/7 Monitoring):</b>\n\n"
        "● <b>Оркестратор NEXUS:</b> 🟢 Активен\n"
        "● <b>Агенты (SCOUT, SWING, SHADOW, ARBITRAGE):</b> 🟢 Готовы\n"
        f"● <b>Telegram Слушатель:</b> 🟢 Активен (5 мин)\n"
        f"● <b>Trend Hunter:</b> {'🟢 Активен (2 ч)' if trend_hunter_enabled else '🔴 Отключен'}\n"
        f"● <b>Тренды-оповещения:</b> {'🟢 Включены' if trend_hunter_alerts else '🔴 Отключены'}\n"
        f"● <b>База данных:</b> {'🟢 OK' if DB_PATH.exists() else '🔴 Ошибка'}\n"
        f"● <b>Лимит запросов:</b> <code>{scan_limit} рынков/цикл</code>\n"
        f"● <b>Текущее действие:</b> {'🟡 Сканирование...' if is_scanning_real else '🟢 Ожидание'}\n\n"
        f"🧠 <b>Память:</b>\n"
        f"  Факты (Layer 1): {stats.get('facts', '?')}\n"
        f"  Рынков в БД: {stats.get('markets', '?')}\n"
        f"  Сигналов (активных): {stats.get('signals_pending', '?')}\n"
        f"  Сигналов (архив): {stats.get('signals_archived', '?')}\n"
        f"  Мнений агентов: {stats.get('opinions', '?')}\n"
        f"  Vault файлов: {stats.get('vault_files', '?')}\n"
        f"  Размер БД: {stats.get('db_size_kb', 0):.0f} KB\n\n"
        f"🕒 <b>Последнее авто-сканирование:</b>\n<code>{last_scan_str}</code>\n"
        f"🎯 <b>Последний поиск трендов:</b>\n<code>{trend_hunter_last_run}</code>"
    )

    # Точность SCOUT
    from agents.shared.python.db import get_memory
    accuracy = get_memory("scout_accuracy_pct")
    evaluated = get_memory("scout_evaluated_total") or 0
    accuracy_line = "\n\n🎯 <b>Точность SCOUT:</b> "
    if accuracy and evaluated > 0:
        accuracy_line += f"<b>{accuracy}%</b> (по {evaluated} сигналам)"
    else:
        accuracy_line += "накапливается..."
    status_text += accuracy_line

    if is_scanning_real:
        # Получаем детальный статус сканирования
        state = engine.state
        if isinstance(state, dict):
            category = state.get("category", "Авто-микс")
            stage = state.get("stage", "В процессе")
            cur_idx = state.get('current_market_index', 0)
            tot = state.get('total_markets', 0)
            title = state.get('current_market_title', 'Поиск...')
            url = state.get('current_market_url', '')
            scout = state.get('scout_status', '⏳ Ожидает')
            swing = state.get('swing_status', '⏳ Ожидает')
            shadow = state.get('shadow_status', '⏳ Ожидает')
            ideas = state.get('ideas_found', 0)

            market_link = f"<a href='{url}'>{title}</a>" if url else f"<b>{title}</b>"

            progress_line = ""
            if tot > 0:
                progress_line = f"● 📊 <b>Прогресс:</b> Рынок <code>{cur_idx}</code> из <code>{tot}</code>\n"

            status_text += (
                f"\n\n⚡️ <b>Детали текущего сканирования:</b>\n"
                f"● 📋 <b>Категория:</b> {category}\n"
                f"● ⚙️ <b>Этап:</b> {stage}\n"
                f"{progress_line}"
                f"● 🎯 <b>Активный рынок:</b> {market_link}\n"
                f"● 🕵️‍♂️ <b>SCOUT:</b> {scout}\n"
                f"● 🚀 <b>SWING:</b> {swing}\n"
                f"● 👤 <b>SHADOW:</b> {shadow}\n"
                f"● <i>💡 Найдено идей (консенсус): {ideas}</i>"
            )

    await message.answer(status_text)

@dp.message(Command("performance"))
async def cmd_performance(message: types.Message):
    from agents.shared.python.db import get_performance_summary
    scout_stats = await asyncio.to_thread(get_performance_summary, "SCOUT", 10)
    swing_stats = await asyncio.to_thread(get_performance_summary, "SWING", 10)
    shadow_stats = await asyncio.to_thread(get_performance_summary, "SHADOW", 10)
    
    text = "📊 <b>Производительность агентов:</b>\n\n"
    if scout_stats:
        text += f"<b>SCOUT:</b>\n{scout_stats}\n\n"
    if swing_stats:
        text += f"<b>SWING:</b>\n{swing_stats}\n\n"
    if shadow_stats:
        text += f"<b>SHADOW:</b>\n{shadow_stats}\n\n"
        
    if text == "📊 <b>Производительность агентов:</b>\n\n":
        text += "Пока нет завершённых прогнозов."
    
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def command_stats_handler(message: types.Message) -> None:
    await message.answer(await asyncio.to_thread(get_db_stats))

@dp.message(Command("audit"))
async def command_audit_handler(message: types.Message) -> None:
    from agents.shared.python.db import get_connection
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT final_outcome, COUNT(*) as cnt
                FROM idea_audit
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY final_outcome
            """).fetchall()
            
            shadow_rejection = conn.execute("""
                SELECT COUNT(*) FROM idea_audit
                WHERE shadow_agree = 0 AND scout_edge IS NOT NULL
                AND created_at >= datetime('now', '-24 hours')
            """).fetchone()[0]
        
        text = "📊 <b>Audit Pipeline (24ч):</b>\n\n"
        if not rows:
            text += "<i>Нет данных за последние 24 часа.</i>\n"
        for row in rows:
            outcome_icons = {
                "saved": "✅", "no_consensus": "🛑", 
                "no_signal": "⚪️", "skipped_cooldown": "⏭"
            }
            icon = outcome_icons.get(row["final_outcome"], "❓")
            text += f"{icon} {row['final_outcome']}: <b>{row['cnt']}</b>\n"
        
        text += f"\n🔴 Отклонено SHADOW: <b>{shadow_rejection}</b>"
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Ошибка получения аудита: {e}")

async def send_settings_menu(message_or_callback):
    from agents.shared.python.db import get_memory
    current_edge = int((get_memory("min_edge") or 0.10) * 100)
    try:
        rag_level = get_memory("rag_level")
        rag_level = int(rag_level) if rag_level is not None else 2
    except Exception:
        rag_level = 2
    
    rag_labels = {1: "Быстрый (L1)", 2: "Стандарт (L2)", 3: "Глубокий (L3)"}
    rag_text = rag_labels.get(rag_level, "Стандарт (L2)")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Лимит рынков (scan_limit)", callback_data="settings_limits")],
        [InlineKeyboardButton(text=f"🎯 Edge порог: {current_edge}%", callback_data="settings_edge")],
        [InlineKeyboardButton(text=f"🧠 RAG-глубина: {rag_text}", callback_data="settings_rag")],
        [InlineKeyboardButton(text="🤖 Модели агентов (LLM)", callback_data="settings_models")],
        [InlineKeyboardButton(text="🔎 Настройки Trend Hunter", callback_data="settings_trend_hunter")],
    ])
    await send_or_edit(message_or_callback, "⚙️ <b>Настройки системы:</b>\n\nВыберите параметр для настройки:", keyboard)

@dp.message(Command("settings"))
async def command_settings_handler(message: types.Message) -> None:
    await send_settings_menu(message)

@dp.callback_query(F.data == "settings_trend_hunter")
async def callback_settings_trend_hunter(callback: CallbackQuery) -> None:
    from agents.shared.python.db import get_memory
    enabled = get_memory("trend_hunter_enabled", True)
    alerts = get_memory("trend_hunter_alerts_enabled", True)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 Охотник: {'🟢 Вкл' if enabled else '🔴 Выкл'}", callback_data="toggle_trend_hunter")],
        [InlineKeyboardButton(text=f"🔔 Оповещения: {'🟢 Вкл' if alerts else '🔴 Выкл'}", callback_data="toggle_trend_alerts")],
        [InlineKeyboardButton(text="🚀 Запустить поиск сейчас", callback_data="trigger_trend_hunter")],
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="back_to_settings")]
    ])
    
    await callback.message.edit_text(
        "🔎 <b>Настройки проактивного Trend Hunter:</b>\n\n"
        "Служба автоматически парсит бесплатные ленты новостей Google News и тренды Google Trends раз в 2 часа, извлекает новые мировые события через ИИ NEXUS, ищет соответствующие рынки на Polymarket и мгновенно триггерит их точечный командный анализ.\n\n"
        "Выберите параметр или запустите вручную:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data == "toggle_trend_hunter")
async def callback_toggle_trend_hunter(callback: CallbackQuery) -> None:
    from agents.shared.python.db import get_memory, save_memory
    current = get_memory("trend_hunter_enabled", True)
    new_val = not current
    await asyncio.to_thread(save_memory, "trend_hunter_enabled", new_val)
    await callback_settings_trend_hunter(callback)

@dp.callback_query(F.data == "toggle_trend_alerts")
async def callback_toggle_trend_alerts(callback: CallbackQuery) -> None:
    from agents.shared.python.db import get_memory, save_memory
    current = get_memory("trend_hunter_alerts_enabled", True)
    new_val = not current
    await asyncio.to_thread(save_memory, "trend_hunter_alerts_enabled", new_val)
    await callback_settings_trend_hunter(callback)


@dp.callback_query(F.data == "trigger_trend_hunter")
async def callback_trigger_trend_hunter(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🚀 <b>Принудительный запуск Trend Hunter...</b>\n\n"
        "Служба запущена в фоновом режиме. Она скачает свежие RSS Google News/Trends, извлечет тренды через NEXUS и проверит активные рынки на Polymarket.\n\n"
        "Если найдутся новые рынки, они сразу отправятся на командный анализ ИИ и вы получите уведомление!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к Охотнику", callback_data="settings_trend_hunter")]
        ])
    )
    await callback.answer()
    
    # Запускаем в фоновом потоке
    from services.trend_hunter import run_trend_hunter
    async def _run_trend_hunter_safe():
        try:
            await asyncio.to_thread(run_trend_hunter)
        except Exception as e:
            logging.error(f"[TrendHunter] Необработанное исключение: {e}", exc_info=True)
            from agents.shared.python.db import get_memory
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if chat_id:
                try:
                    await bot.send_message(chat_id=chat_id, text=f"⚠️ TrendHunter упал: {e}")
                except:
                    pass

    asyncio.create_task(_run_trend_hunter_safe())

@dp.callback_query(F.data == "back_to_settings")
async def callback_back_to_settings(callback: CallbackQuery) -> None:
    await send_settings_menu(callback)

@dp.callback_query(F.data == "settings_rag")
async def callback_settings_rag(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Быстрый (L1 - 2 док., 15 строк)", callback_data="setrag_1")],
        [InlineKeyboardButton(text="Стандарт (L2 - 4 док., 30 строк)", callback_data="setrag_2")],
        [InlineKeyboardButton(text="Глубокий (L3 - 8 док., 60 строк)", callback_data="setrag_3")]
    ])
    await callback.message.edit_text(
        "🧠 <b>Глубина RAG-анализа (Obsidian):</b>\n\n"
        "Выберите уровень детализации контекста долгосрочной памяти при принятии решений:\n\n"
        "• <b>L1 (Быстрый):</b> Минимальный контекст, максимальная экономия токенов.\n"
        "• <b>L2 (Стандарт):</b> Оптимальный баланс глубины и стоимости (рекомендуется).\n"
        "• <b>L3 (Глубокий):</b> Максимальный сбор исторических параллелей и заметок.", 
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("setrag_"))
async def callback_set_rag(callback: CallbackQuery) -> None:
    level = int(callback.data.split("_")[1])
    from agents.shared.python.db import save_memory
    await asyncio.to_thread(save_memory, "rag_level", level)
    await callback.answer(f"RAG-уровень установлен на L{level}!")
    await send_settings_menu(callback)

def get_nice_model_name(model_id: str) -> str:
    """Возвращает красивое форматированное имя модели с эмодзи."""
    model_lower = model_id.lower()
    
    # Извлекаем базовое имя
    display_name = model_id.split("/")[-1] if "/" in model_id else model_id
    
    if "gemini-2.5-flash" in model_lower:
        return "✨ Gemini 2.5 Flash"
    elif "gemini-2.0-flash-lite" in model_lower:
        return "⚡ Gemini 2.0 Flash Lite"
    elif "gemini-2.0-flash-exp" in model_lower:
        return "🧪 Gemini 2.0 Flash Exp"
    elif "gemini-2.0-flash-thinking" in model_lower:
        return "🤔 Gemini Thinking"
    elif "gemini-2.0-flash" in model_lower:
        return "⚡ Gemini 2.0 Flash"
    elif "gemini-2.5-pro" in model_lower:
        return "🧠 Gemini 2.5 Pro"
    elif "llama-3.3" in model_lower:
        return "🦙 Llama 3.3"
    elif "nemotron" in model_lower:
        return "🟢 Nemotron 3 (Free)"
    elif "glm-4.5-air" in model_lower or "glm_45" in model_lower:
        return "🟣 GLM 4.5 Air (Free)"
    elif "qwen" in model_lower:
        return "🤖 Qwen"
    elif "cerebras_round_robin" in model_lower:
        return "⚡ Cerebras (Round Robin)"
    elif "cerebras" in model_lower:
        return f"⚡ Cerebras ({display_name})"
        
    formatted = display_name.replace(":free", "").replace("-instruct", "").title()
    if "gemini" in model_lower:
        return f"✨ {formatted}"
    return formatted

def _shorten_key(key: str) -> str:
    """
    Сокращает ключ модели с использованием хэша, если его длина превышает 30 символов,
    чтобы гарантировать длину callback_data (sm_{agent}_{key}) <= 64 байт.
    """
    if len(key) > 30:
        import hashlib
        h = hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
        return f"{key[:20]}_{h}"
    return key

def get_dynamic_models_mapping() -> dict:
    """Динамически формирует маппинг на основе доступных моделей в PROVIDERS_CONFIG."""
    from agents.shared.utils.gemini_client import PROVIDERS_CONFIG
    
    mapping = {}
    
    # 1. Gemini
    for m in PROVIDERS_CONFIG["gemini"]["models"]:
        key = m.replace("-", "_").replace(".", "_")
        mapping[_shorten_key(key)] = ("gemini", m, get_nice_model_name(m))
        
    # Принудительно добавляем Thinking, если его нет в PROVIDERS_CONFIG
    think_model = "gemini-2.0-flash-thinking-exp-01-21"
    if not any(v[1] == think_model for v in mapping.values()):
        mapping[_shorten_key("geminithink")] = ("gemini", think_model, get_nice_model_name(think_model))
        
    # 2. OpenRouter
    or_models = list(PROVIDERS_CONFIG["openrouter"]["models"])
    # Добавляем популярные модели, чтобы они всегда были в списке переключения
    for m in ["meta-llama/llama-3.3-70b-instruct:free", "nvidia/nemotron-3-super-120b-a12b:free", "z-ai/glm-4.5-air:free"]:
        if m not in or_models:
            or_models.append(m)
            
    for m in or_models:
        suffix = m.split("/")[-1].split(":")[0].replace("-", "_").replace(".", "_")
        key = f"or_{suffix}"
        mapping[_shorten_key(key)] = ("openrouter", m, get_nice_model_name(m))
        
    # 3. Cerebras
    mapping[_shorten_key("cerebras")] = ("cerebras", "cerebras_round_robin", "⚡ Cerebras (Round Robin)")
    for m in PROVIDERS_CONFIG["cerebras"]["models"]:
        key = "cerebras_" + m.replace("-", "_").replace(".", "_")
        mapping[_shorten_key(key)] = ("cerebras", m, f"⚡ Cerebras ({m})")
    
    return mapping

def get_configured_agent_model(agent: str, default_model: str) -> str:
    """Возвращает настроенную вручную модель для агента, либо дефолтную."""
    from agents.shared.python.db import get_memory
    config = get_memory(f"agent_config_{agent}")
    if config and isinstance(config, dict) and config.get("model"):
        return config["model"]
    if agent == "NEXUS":
        selected_model = get_memory("selected_model")
        if selected_model:
            return selected_model
    return default_model

async def send_models_menu(message_or_callback):
    from agents.shared.python.db import get_memory, get_detailed_token_usage_last_24h
    
    agents = ["NEXUS", "SCOUT", "SWING", "SHADOW", "ARBITRAGE"]
    default_models = {
        "NEXUS": "gemini-2.5-flash",
        "SCOUT": "gemini-2.5-flash",
        "SWING": "gemini-2.5-flash",
        "SHADOW": "gemini-2.5-flash",
        "ARBITRAGE": "gemini-2.5-pro"
    }
    
    # Сбор данных о моделях параллельно
    def fetch_model(agent):
        return get_configured_agent_model(agent, default_models[agent])

    active_models_tasks = [asyncio.to_thread(fetch_model, agent) for agent in agents]
    token_usage_tasks = [asyncio.to_thread(get_detailed_token_usage_last_24h, agent) for agent in agents]

    active_models = await asyncio.gather(*active_models_tasks)
    usages = await asyncio.gather(*token_usage_tasks)

    models_by_agent = dict(zip(agents, active_models))
    usages_by_agent = dict(zip(agents, usages))
    
    dashboard_lines = []
    dashboard_lines.append("🤖 <b>Панель AI Моделей и Расходов (24ч):</b>\n")
    
    for agent in agents:
        active_model = models_by_agent[agent]
        dashboard_lines.append(f"● <b>Агент {agent}</b>")
        dashboard_lines.append(f"  Активная модель: <code>{get_nice_model_name(active_model)}</code>")
        
        usage = usages_by_agent[agent]
        
        # Фильтруем модели с 0 токенов, чтобы динамически скрывать неиспользуемые
        usage_active = [item for item in usage if item.get("total_tokens", 0) > 0]
        
        agent_cost = 0.0
        if not usage_active:
            dashboard_lines.append("  <i>Нет успешных вызовов за последние 24ч</i>")
        else:
            for item in usage_active:
                m_name = item["model_name"]
                in_t = item["input_tokens"]
                out_t = item["output_tokens"]
                tot_t = item["total_tokens"]
                
                cost = estimate_llm_cost(m_name, in_t, out_t)
                agent_cost += cost
                
                # Получаем красивое имя модели
                display_name = get_nice_model_name(m_name)
                if len(display_name) > 35:
                    display_name = display_name[:32] + "..."
                
                dashboard_lines.append(f"  - <code>{display_name}</code>: {tot_t:,} токенов (${cost:.4f})")
                
        dashboard_lines.append(f"  <b>Всего за сутки:</b> <code>${agent_cost:.4f}</code>\n")

    dashboard_lines.append("Выберите агента для переназначения активной LLM:")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 NEXUS", callback_data="set_model_NEXUS"),
            InlineKeyboardButton(text="🔄 SCOUT", callback_data="set_model_SCOUT"),
        ],
        [
            InlineKeyboardButton(text="🔄 SWING", callback_data="set_model_SWING"),
            InlineKeyboardButton(text="🔄 SHADOW", callback_data="set_model_SHADOW"),
        ],
        [
            InlineKeyboardButton(text="🔄 ARBITRAGE", callback_data="set_model_ARBITRAGE"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="back_to_settings")]
    ])
    
    text = "\n".join(dashboard_lines)
    await send_or_edit(message_or_callback, text, keyboard)

@dp.callback_query(F.data == "settings_models")
async def callback_settings_models(callback: CallbackQuery) -> None:
    await send_models_menu(callback)

@dp.callback_query(F.data.startswith("set_model_"))
async def callback_set_agent_model(callback: CallbackQuery) -> None:
    agent = callback.data.split("_")[2]
    from agents.shared.python.db import get_memory
    current_config = get_memory(f"agent_config_{agent}", {})
    current_model_id = current_config.get("model", "Дефолт (.env)")
    
    # Пытаемся найти красивое имя модели
    nice_model_name = get_nice_model_name(current_model_id)
    is_default = False
    
    if current_model_id == "Дефолт (.env)":
        # Если ручная модель не задана, по умолчанию используется Gemini 2.5 Flash
        current_model_id = "gemini-2.5-flash"
        is_default = True
        nice_model_name = get_nice_model_name(current_model_id)
        
    models_mapping = get_dynamic_models_mapping()
    for k, v in models_mapping.items():
        if v[1] == current_model_id:
            nice_model_name = v[2]
            break
            
    if is_default:
        nice_model_name += " (По умолчанию)"
            
    buttons = []
    for key, val in models_mapping.items():
        buttons.append([InlineKeyboardButton(text=val[2], callback_data=f"sm_{agent}_{key}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_models")])
    
    await callback.message.edit_text(
        f"🤖 <b>Настройка модели для: {agent}</b>\n\n"
        f"Текущая ручная модель: <code>{nice_model_name}</code>\n\n"
        f"Выберите новую модель:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("sm_"))
async def callback_save_model(callback: CallbackQuery) -> None:
    parts = callback.data.split("_")
    agent = parts[1]
    model_key = "_".join(parts[2:])
    
    models_mapping = get_dynamic_models_mapping()
    provider, model_name, _ = models_mapping.get(model_key, ("openrouter", "meta-llama/llama-3.3-70b-instruct:free", "🦙 Llama 3.3"))
    
    from agents.shared.python.db import save_memory
    config = {"provider": provider, "model": model_name}
    await asyncio.to_thread(save_memory, f"agent_config_{agent}", config)
    
    await callback.answer(f"✅ Модель установлена!", show_alert=True)
    await send_models_menu(callback)


@dp.callback_query(F.data == "settings_limits")
async def callback_settings_limits(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Минимум (3 рынка)", callback_data="setlimit_3")],
        [InlineKeyboardButton(text="Эконом (5 рынков)", callback_data="setlimit_5")],
        [InlineKeyboardButton(text="Стандарт (10 рынков)", callback_data="setlimit_10")],
        [InlineKeyboardButton(text="Глубокий (20 рынков)", callback_data="setlimit_20")]
    ])
    await callback.message.edit_text("⚙️ <b>Лимит рынков за цикл:</b>\n\nЧем меньше число, тем дешевле работа агентов.", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "settings_edge")
async def callback_settings_edge(callback: CallbackQuery) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5% (агрессивный — больше сигналов)", callback_data="edge_5")],
        [InlineKeyboardButton(text="7% (умеренный)", callback_data="edge_7")],
        [InlineKeyboardButton(text="10% (стандарт)", callback_data="edge_10")],
        [InlineKeyboardButton(text="15% (консервативный — только явные)", callback_data="edge_15")]
    ])
    await callback.message.edit_text("🎯 <b>Edge порог (SCOUT):</b>\n\nМинимальное математическое преимущество, при котором SCOUT сгенерирует сигнал.\nНиже = больше сигналов (но менее надёжных).", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("edge_"))
async def callback_edge_handler(callback: CallbackQuery) -> None:
    value = int(callback.data.split("_")[1])
    from agents.shared.python.db import save_memory
    await asyncio.to_thread(save_memory, "min_edge", value / 100, 'config', None, 10)
    await callback.message.edit_text(f"✅ <b>Edge порог обновлён!</b>\nSCOUT будет генерировать сигналы при преимуществе ≥ <b>{value}%</b>.")
    await callback.answer()

@dp.callback_query(F.data.startswith("setlimit_"))
async def callback_setlimit_handler(callback: CallbackQuery) -> None:
    limit = int(callback.data.split("_")[1])
    from agents.shared.python.db import save_memory
    await asyncio.to_thread(save_memory, "scan_limit", limit)
    await callback.message.edit_text(f"✅ <b>Лимит обновлен!</b>\nТеперь система будет анализировать не более <b>{limit}</b> рынков за один проход.")
    await callback.answer()

@dp.message(Command("model"))
async def command_model_handler(message: types.Message) -> None:
    await send_models_menu(message)

@dp.callback_query(F.data.startswith("setmodel_"))
async def callback_setmodel_handler(callback: CallbackQuery) -> None:
    model_name = callback.data.split("_")[1]
    from agents.shared.python.db import save_memory
    await asyncio.to_thread(save_memory, "selected_model", model_name)
    await callback.message.edit_text(f"✅ <b>Модель обновлена!</b>\nТеперь Оркестратор будет использовать: <b>{model_name}</b>")
    await callback.answer()

@dp.message(Command("logs"))
async def command_logs_handler(message: types.Message) -> None:
    if not LOG_PATH.exists():
        await message.answer("Лог-файл еще не создан.")
        return
    
    try:
        # Читаем последние 10 строк нативно (без `tail`, которого нет на Windows)
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            last_lines = all_lines[-10:]
            logs = "".join(last_lines)
        # Экранируем спецсимволы для HTML
        safe_logs = logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        await message.answer(f"📜 <b>Последние логи:</b>\n<pre>{safe_logs}</pre>")
    except Exception as e:
        await message.answer(f"Ошибка чтения логов: {e}")

@dp.message(Command("restart"))
async def command_restart_handler(message: types.Message) -> None:
    """Останавливает процесс бота. Менеджер процессов (systemd/PM2) автоматически его перезапустит."""
    
    expected_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if expected_chat_id and str(message.chat.id) != expected_chat_id:
        await message.answer("❌ Нет доступа.")
        return
        
    await message.answer("🔄 <b>Перезапуск бота через 3 секунды...</b>\nСлужба завершает процессы.", parse_mode="HTML")
    logging.warning("Получена команда /restart. Закрываю сессию и завершаю процесс...")
    
    import asyncio
    await asyncio.sleep(3)
    
    # Изящное завершение: закрываем сессию Telegram, чтобы ОС успела снять файловую блокировку
    try:
        await bot.session.close()
    except Exception:
        pass
        
    import sys
    sys.exit(0)

@dp.message(Command("arbitrage"))
async def command_arbitrage_handler(message: types.Message) -> None:
    status_msg = await message.answer("🔄 <b>Запускаю кросс-сканирование Polymarket ↔ Kalshi...</b>\n\n<i>Этот процесс занимает 1-2 минуты, так как агент ARBITRAGE сверяет десятки пар.</i>", parse_mode="HTML")
    
    try:
        import traceback as tb
        from core.arbitrage_workflow import run_cross_platform_scan
        api_key = os.getenv("GOOGLE_API_KEY")
        found = await asyncio.to_thread(
            run_cross_platform_scan,
            api_key=api_key,
            poly_limit=100,
            kalshi_limit=100,
            min_spread_alert=3.0,
        )
        
        if not found:
            await status_msg.edit_text("⚖️ Сканирование завершено. <b>Безрисковых арбитражных связок с достаточным спредом не найдено.</b>", parse_mode="HTML")
            return
            
        response = f"🔥 <b>НАЙДЕНО КРОСС-АРБИТРАЖНЫХ ИДЕЙ: {len(found)}</b>\n\n"
        for i, s in enumerate(found[:10]):
            response += (
                f"<b>{i+1}. {s.arbitrage_type}</b> (Спред: <b>{s.spread_percent:.1f}%</b>)\n"
                f"🔵 PM: <a href='{s.market_a_url}'>{s.market_a_price*100:.0f}¢</a> | 🟢 Kalshi: <a href='{s.market_b_url}'>{s.market_b_price*100:.0f}¢</a>\n"
                f"💡 Обоснование: {s.reasoning}\n"
                f"🎯 Действие: <b>{s.trade_instruction}</b>\n\n"
            )
            
        await status_msg.edit_text(response, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        
    except Exception as e:
        error_text = tb.format_exc()[-800:]  # последние 800 символов трассировки
        logging.error(f"[ARBITRAGE] Ошибка: {error_text}")
        await status_msg.edit_text(f"❌ <b>Ошибка арбитражного сканирования:</b>\n<pre>{str(e)[:400]}</pre>", parse_mode="HTML")

@dp.message(Command("corridor"))
async def command_corridor_handler(message: types.Message) -> None:
    status_msg = await message.answer("🕐 <b>Сканирую временные коридоры...</b>", parse_mode="HTML")
    try:
        from services.temporal_corridor_scanner import run_temporal_corridor_scan
        signals = await asyncio.to_thread(
            run_temporal_corridor_scan, poly_limit=100, budget=200.0
        )
        if not signals:
            await status_msg.edit_text("🕐 Временных коридоров с положительным EV не найдено.")
            return

        text = f"🕐 <b>Временные коридоры — найдено {len(signals)}:</b>\n\n"
        for s in signals[:5]:
            text += (
                f"📍 <b>{s.event_title[:50]}</b>\n"
                f"📅 NO до <b>{s.early_leg.expiry.strftime('%d %b')}</b> "
                f"({s.early_leg.entry_cost*100:.0f}¢) "
                f"+ YES до <b>{s.late_leg.expiry.strftime('%d %b')}</b> "
                f"({s.late_leg.entry_cost*100:.0f}¢)\n"
                f"📊 P(коридор)=<b>{s.p_in_corridor*100:.0f}%</b> "
                f"| gap=<b>{s.date_gap_days}д</b>\n"
                f"💰 Реальный спред: <b>+{s.real_spread_pct:.1f}%</b> "
                f"| Q-score: <b>{s.quality_score:.2f}</b>\n"
                f"🎯 S1=${s.pnl_s1_before_early:.0f} | "
                f"S2=<b>${s.pnl_s2_in_corridor:.0f}</b> | "
                f"S3=${s.pnl_s3_never:.0f}\n"
                f"💵 EV: <b>${s.ev_usd:.2f}</b> (бюджет ${s.early_stake_usd + s.late_stake_usd:.0f})\n"
                f"🚪 {s.exit_rule[:100]}\n"
                f"🔗 <a href='{s.event_url}'>Открыть</a>\n\n"
            )
        await status_msg.edit_text(text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception as e:
        logger.error(f"[TC] Ошибка в команде /corridor: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка сканирования: {e}")


@dp.message(Command("cleanup"))
async def command_cleanup_handler(message: types.Message) -> None:
    """Очищает устаревшие сигналы и проверяет результаты (WIN/LOSS)."""
    from agents.shared.python.resolution import resolve_closed_markets
    
    await message.answer("🔄 Начинаю проверку закрытых рынков с API Polymarket...")
    count = await asyncio.to_thread(resolve_closed_markets)
    await message.answer(f"🧹 Проверка завершена. Рассчитано рынков: {count}. Результаты добавлены в /history")

@dp.message(Command("correlations"))
async def command_correlations_handler(message: types.Message) -> None:
    """Анализирует найденные корреляции между рынками с помощью LLM."""
    from agents.shared.python.db import get_new_correlations, get_market_correlations
    from agents.polymarket_arbitrage_agent.src.agent import ArbitrageAgent
    from agents.shared.adapters.polymarket import PolymarketAdapter
    from services.notifications import format_cross_arbitrage_alert
    import os

    corrs = get_new_correlations()[:10]   # только новые для алертов
    if not corrs:
        await message.answer("✅ Новых корреляций нет.")
        return

    await message.answer(f"🔍 Анализирую {len(corrs)} корреляций, ~30 сек...")

    adapter = PolymarketAdapter()
    agent = ArbitrageAgent(api_key=os.getenv("GOOGLE_API_KEY"))

    def process_correlations(corrs_to_process):
        found_signals = []
        for c in corrs_to_process:
            try:
                market_a = adapter.get_market(c["market_id_a"])
                market_b = adapter.get_market(c["market_id_b"])
            except Exception:
                continue
            if not market_a or not market_b:
                continue

            signal = agent.analyze_correlation(
                market_a=market_a,
                market_b=market_b,
                correlation_type=c["correlation_type"],
                score=int(float(c["confidence"]) * 100),
            )
            import time
            time.sleep(3)  # Избегаем rate-limit
            
            if signal and signal.has_arbitrage:
                found_signals.append(signal)
        return found_signals

    found_signals = await asyncio.to_thread(process_correlations, corrs)
    found = len(found_signals)

    for signal in found_signals:
        text = format_cross_arbitrage_alert(signal)
        await message.answer(text, link_preview_options=LinkPreviewOptions(is_disabled=True))

    summary = (
        f"✅ Найдено торговых идей: <b>{found}</b> из {len(corrs)} корреляций."
        if found > 0
        else "✅ Арбитражных возможностей в текущих корреляциях не обнаружено."
    )
    await message.answer(summary)


def get_active_scan_status_text() -> str:
    """
    Формирует подробный HTML-отчет о текущем прогрессе сканирования и статусах агентов.
    """
    try:
        engine = get_core_engine()
        state = engine.state
    except Exception as e:
        logger.error(f"Ошибка получения статуса сканирования из CoreEngine: {e}")
        return "⚠️ <b>Сканирование запущено.</b>\n<i>Информацию о текущем рынке и агентах получить не удалось, так как система инициализируется. Пожалуйста, подождите...</i> 🔄"

    category = state.get("category", "Авто-микс")
    stage = state.get("stage", "В процессе")
    cur_idx = state.get('current_market_index', 0)
    tot = state.get('total_markets', 0)
    title = state.get('current_market_title', 'Поиск...')
    url = state.get('current_market_url', '')
    scout = state.get('scout_status', '⏳ Ожидает')
    swing = state.get('swing_status', '⏳ Ожидает')
    shadow = state.get('shadow_status', '⏳ Ожидает')
    ideas = state.get('ideas_found', 0)

    market_link = f"<a href='{url}'>{title}</a>" if url else f"<b>{title}</b>"

    progress_line = ""
    if tot > 0:
        progress_line = f"● 📊 <b>Прогресс:</b> Рынок <code>{cur_idx}</code> из <code>{tot}</code>\n"

    return (
        f"⚠️ <b>Сканирование уже запущено. Пожалуйста, подождите.</b>\n\n"
        f"● 📋 <b>Категория:</b> {category}\n"
        f"● ⚙️ <b>Этап:</b> {stage}\n"
        f"{progress_line}"
        f"● 🎯 <b>Активный рынок:</b> {market_link}\n\n"
        f"🕵️‍♂️ <b>SCOUT:</b> {scout}\n"
        f"🚀 <b>SWING:</b> {swing}\n"
        f"👤 <b>SHADOW:</b> {shadow}\n\n"
        f"<i>💡 Найдено идей (консенсус): {ideas}</i>"
    )


@dp.message(Command("scan"))
async def command_scan_handler(message: types.Message) -> None:
    engine = get_core_engine()
    if _scan_lock.locked() or engine._scan_lock.locked():
        status_text = get_active_scan_status_text()
        await message.answer(status_text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Все (авто-микс)", callback_data="scan_all")],
        [InlineKeyboardButton(text="🏛 Политика", callback_data="scan_politics"),
         InlineKeyboardButton(text="₿ Крипто", callback_data="scan_crypto")],
        [InlineKeyboardButton(text="⚽ Спорт", callback_data="scan_sports"),
         InlineKeyboardButton(text="🔬 Наука", callback_data="scan_science")],
        [InlineKeyboardButton(text="💼 Бизнес", callback_data="scan_business")],
        [InlineKeyboardButton(text="🪙 Penny Stocks (1-5%)", callback_data="scan_penny_stocks")]
    ])
    await message.answer("🔍 <b>Выберите категорию для сканирования:</b>", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("scan_"))
async def callback_scan_handler(callback: CallbackQuery) -> None:
    engine = get_core_engine()
    if _scan_lock.locked() or engine._scan_lock.locked():
        await callback.answer()
        status_text = get_active_scan_status_text()
        await callback.message.answer(status_text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        return

    category = callback.data.replace("scan_", "")
    if category == "all":
        category_param = None
        cat_name = "Все рынки (авто-микс)"
    else:
        category_param = category
        cat_map = {
            "politics": "🏛 Политика", "crypto": "₿ Крипто",
            "sports": "⚽ Спорт", "science": "🔬 Наука",
            "culture": "🎬 Культура", "business": "💼 Бизнес",
            "penny_stocks": "🪙 Penny Stocks"
        }
        cat_name = cat_map.get(category, category)

    async with _scan_lock:
        await callback.message.edit_text(f"🚀 Запускаю полный цикл анализа (Категория: {cat_name})...")
        await callback.answer("🔄 Сканирование запущено...")
        status_msg = callback.message
        
        log_lines = []
        def log_callback(text):
            log_lines.append(text)
    
        current_state = {}
        def state_callback(state):
            nonlocal current_state
            current_state = state
    
        def render_dashboard(state):
            if not state:
                return f"🚀 <b>Запуск сканирования (Категория: {cat_name})...</b>"
                
            html = f"🚀 <b>Сканирование рынков</b>\n"
            html += f"<b>Категория:</b> {state.get('category', cat_name)}\n"
            html += f"<b>Этап:</b> {state.get('stage', 'В процессе')}\n"
            
            total = state.get('total_markets', 0)
            if total > 0:
                html += f"<b>Прогресс:</b> Рынок {state.get('current_market_index', 0)} из {total}\n\n"
            else:
                html += "\n"
                
            title = state.get('current_market_title', '')
            url = state.get('current_market_url', '')
            if title:
                if url:
                    html += f"<b>Текущий рынок:</b>\n<a href='{url}'>{title}</a>\n\n"
                else:
                    html += f"<b>Текущий рынок:</b>\n{title}\n\n"
                    
            html += "<b>Статус агентов:</b>\n"
            html += f"🕵️‍♂️ <b>SCOUT:</b> {state.get('scout_status', '⏳ Ожидает')}\n"
            html += f"🚀 <b>SWING:</b> {state.get('swing_status', '⏳ Ожидает')}\n"
            html += f"👤 <b>SHADOW:</b> {state.get('shadow_status', '⏳ Ожидает')}\n\n"
            
            html += f"<i>💡 Найдено идей (консенсус): {state.get('ideas_found', 0)}</i>"
            return html
    
        summaries_queue = []
        def summary_callback(text, reply_markup=None):
            summaries_queue.append((text, reply_markup))
    
        async def update_message():
            last_text = ""
            start_time = asyncio.get_running_loop().time()
            max_duration_sec = 1800  # Таймаут 30 минут
            while _scan_lock.locked() or summaries_queue:
                if asyncio.get_running_loop().time() - start_time > max_duration_sec:
                    logging.getLogger("NexusPolyBot").warning("Превышен лимит времени работы (30 мин) для update_message, принудительное завершение задачи обновления.")
                    break
                await asyncio.sleep(2)
                # Send all pending summaries
                while summaries_queue:
                    summary, reply_markup = summaries_queue.pop(0)
                    actual_markup = None
                    if reply_markup:
                        if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
                            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                            keyboard_rows = []
                            for row in reply_markup["inline_keyboard"]:
                                keyboard_row = []
                                for btn in row:
                                    keyboard_row.append(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
                                keyboard_rows.append(keyboard_row)
                            actual_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
                        else:
                            actual_markup = reply_markup
                    try:
                        await callback.message.answer(summary, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True), reply_markup=actual_markup)
                    except Exception as e:
                        print(f"Ошибка отправки summary: {e}")
                
                # Update log status
                if current_state and _scan_lock.locked():
                    new_html = render_dashboard(current_state)
                    if new_html != last_text:
                        try:
                            await status_msg.edit_text(new_html, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
                            last_text = new_html
                        except TelegramRetryAfter as e:
                            logger.warning(f"Flood control in update_message: waiting for {e.retry_after} seconds.")
                            await asyncio.sleep(e.retry_after)
                        except Exception:
                            pass
    
        updater_task = asyncio.create_task(update_message())
        
        try:
            from core.engine import NoMarketsFoundError
            
            SCAN_TIMEOUT_SEC = 1800
            await asyncio.wait_for(
                asyncio.to_thread(engine.run_team_discussion, log_callback, summary_callback, category_param, None, state_callback),
                timeout=SCAN_TIMEOUT_SEC
            )
            if current_state:
                final_html = render_dashboard(current_state)
                await status_msg.edit_text(final_html + "\n\n<b>✅ ПРОЦЕСС ЗАВЕРШЕН</b>", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
            await callback.message.answer("✅ Сканирование завершено! Используйте /ideas чтобы увидеть результат.")
        except NoMarketsFoundError as e:
            if current_state:
                final_html = render_dashboard(current_state)
                try:
                    await status_msg.edit_text(final_html + f"\n\n<b>⚠️ {e}</b>", parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
                except Exception:
                    pass
            await callback.message.answer(f"⚠️ {e}")
        except asyncio.TimeoutError:
            await callback.message.answer(
                "⏱ Сканирование превысило лимит 30 мин.\n"
                "Возможно, Gemini API недоступен. Попробуйте позже."
            )
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка во время сканирования: {e}")
        # Wait a bit to ensure the queue is empty before cancelling
        await asyncio.sleep(2.5)
        updater_task.cancel()
        try:
            await updater_task
        except asyncio.CancelledError:
            pass


async def send_ideas_page(message_or_callback, page: int = 0) -> None:
    signals = await asyncio.to_thread(get_signals, 50)
    if not signals:
        text = "Пока нет новых идей. Запустите /scan."
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer(text)
        else:
            await message_or_callback.message.answer(text)
            await message_or_callback.answer()
        return

    chunk_size = 5
    total_pages = (len(signals) + chunk_size - 1) // chunk_size
    
    if page >= total_pages:
        page = 0
        
    start_idx = page * chunk_size
    chunk = signals[start_idx:start_idx + chunk_size]
    
    response = f"🚀 <b>Торговые сигналы ({start_idx + 1}-{min(start_idx + chunk_size, len(signals))} из {len(signals)}):</b>\n\n"
    
    for s in chunk:
        edge_pct = (s['edge'] or 0) * 100
        target = s.get('target_outcome', 'YES')
        price = s['market_price']
        if target.upper() == 'NO':
            price = 1.0 - price
            
        title_safe = s['title'].replace('<', '&lt;').replace('>', '&gt;')
        summary_safe = s['summary'].replace('<', '&lt;').replace('>', '&gt;')
        if len(summary_safe) > 500:
            summary_safe = summary_safe[:500] + "..."
            
        response += (
            f"📍 <b>{title_safe}</b>\n"
            f"🎯 <b>Рекомендация: Покупать {target}</b> (по цене ~{price:.3f})\n"
            f"📈 Edge (преимущество): <b>+{edge_pct:.1f}%</b> | Уверенность: {s['confidence']}\n"
            f"📝 {summary_safe}\n"
            f"🔗 <a href='{s['url']}'>Открыть рынок</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "ideas_page")
    await send_or_edit(message_or_callback, response, keyboard)

async def send_penny_page(message_or_callback, page: int = 0) -> None:
    signals = await asyncio.to_thread(get_signals, 100)
    # Фильтруем только дешевые
    penny_signals = [s for s in signals if s.get('market_price') and (0.01 <= s['market_price'] <= 0.05 or 0.95 <= s['market_price'] <= 0.99)]
    
    if not penny_signals:
        text = "🪙 Пока нет дешевых опционов в базе. Запустите сканирование."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Запустить сканирование Penny Stocks", callback_data="scan_penny_stocks")]
        ])
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer(text, reply_markup=keyboard)
        else:
            await message_or_callback.message.edit_text(text, reply_markup=keyboard)
            await message_or_callback.answer()
        return

    chunk_size = 5
    total_pages = (len(penny_signals) + chunk_size - 1) // chunk_size
    
    if page >= total_pages:
        page = 0
        
    start_idx = page * chunk_size
    chunk = penny_signals[start_idx:start_idx + chunk_size]
    
    response = f"🪙 <b>Penny Stocks ({start_idx + 1}-{min(start_idx + chunk_size, len(penny_signals))} из {len(penny_signals)}):</b>\n\n"
    
    for s in chunk:
        edge_pct = (s['edge'] or 0) * 100
        target = s.get('target_outcome', 'YES')
        price = s['market_price']
        if target.upper() == 'NO':
            price = 1.0 - price
            
        title_safe = s['title'].replace('<', '&lt;').replace('>', '&gt;')
        summary_safe = s['summary'].replace('<', '&lt;').replace('>', '&gt;')
        if len(summary_safe) > 500:
            summary_safe = summary_safe[:500] + "..."
            
        response += (
            f"📍 <b>{title_safe}</b>\n"
            f"🎯 <b>Рекомендация: Покупать {target}</b> (по цене ~{price:.3f})\n"
            f"📈 Edge (преимущество): <b>+{edge_pct:.1f}%</b> | Уверенность: {s['confidence']}\n"
            f"📝 {summary_safe}\n"
            f"🔗 <a href='{s['url']}'>Открыть рынок</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "penny_page")
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🚀 Искать новые", callback_data="scan_penny_stocks")])
    await send_or_edit(message_or_callback, response, keyboard)

async def send_history_page(message_or_callback, page: int = 0) -> None:
    from agents.shared.python.db import get_history_signals
    signals = await asyncio.to_thread(get_history_signals, 100)
    if not signals:
        text = "🗄 История пуста. Закрытые рынки появятся здесь позже."
        if isinstance(message_or_callback, types.Message):
            await message_or_callback.answer(text)
        else:
            await message_or_callback.message.answer(text)
            await message_or_callback.answer()
        return

    chunk_size = 5
    total_pages = (len(signals) + chunk_size - 1) // chunk_size
    
    if page >= total_pages:
        page = 0
        
    start_idx = page * chunk_size
    chunk = signals[start_idx:start_idx + chunk_size]
    
    response = f"🗄 <b>История (закрытые/истёкшие рынки) ({start_idx + 1}-{min(start_idx + chunk_size, len(signals))} из {len(signals)}):</b>\n\n"
    
    for s in chunk:
        target = s.get('target_outcome', 'YES')
        status = s.get('status', 'ARCHIVED')
        
        status_emoji = "✅" if status == 'WIN' else "❌" if status == 'LOSS' else "🗄"
        
        title_safe = s['title'].replace('<', '&lt;').replace('>', '&gt;')
        summary_safe = s['summary'].replace('<', '&lt;').replace('>', '&gt;')
        if len(summary_safe) > 500:
            summary_safe = summary_safe[:500] + "..."
            
        response += (
            f"{status_emoji} <b>{title_safe}</b>\n"
            f"🎯 Была рекомендация: <b>{target}</b> (Уверенность: {s['confidence']})\n"
            f"📝 {summary_safe}\n"
            f"🔗 <a href='{s['url']}'>Смотреть итог</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "history_page")
    await send_or_edit(message_or_callback, response, keyboard)

@dp.message(Command("ideas"))
async def command_ideas_handler(message: types.Message) -> None:
    await send_ideas_page(message, page=0)

@dp.callback_query(F.data.startswith("ideas_page_"))
async def callback_ideas_page_handler(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_ideas_page(callback, page=page)

@dp.message(Command("history"))
async def command_history_handler(message: types.Message) -> None:
    await send_history_page(message, page=0)

@dp.callback_query(F.data.startswith("history_page_"))
async def callback_history_page_handler(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_history_page(callback, page=page)

@dp.message(Command("penny"))
async def command_penny_handler(message: types.Message) -> None:
    await send_penny_page(message, page=0)

@dp.callback_query(F.data == "close_message")
async def callback_close_message_handler(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data.startswith("penny_page_"))
async def callback_penny_page_handler(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_penny_page(callback, page=page)


# ─────────────────────────────────────────────────────────────────────────────
# Фич Игнорировать / Следить
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("ignore_mkt_"))
async def callback_ignore_market(callback: CallbackQuery) -> None:
    """'Игнорировать' — добавляет рынок в список ignored."""
    market_id = callback.data[len("ignore_mkt_"):]
    market_title = _extract_market_title_from_message(callback.message)
    
    await asyncio.to_thread(add_to_market_list, market_id, market_title, 'ignored', None)
    
    # Заменяем клавиатуру на кнопку "Убрать"
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♻️ Убрать из игнорируемых", callback_data=f"unlist_mkt_{market_id[:40]}")]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception:
        pass
    
    await callback.answer(
        "✅ Рынок добавлен в 'Игнорировать'. При стандартном скане пропускается.",
        show_alert=True
    )


@dp.callback_query(F.data.startswith("watch_mkt_"))
async def callback_watch_market(callback: CallbackQuery) -> None:
    """'Следить' — добавляет рынок в watchlist, запоминает текущую цену как базовую."""
    market_id = callback.data[len("watch_mkt_"):]
    market_title = _extract_market_title_from_message(callback.message)
    
    # Берём последнюю цену из price_history
    base_price = await asyncio.to_thread(_get_last_price_for_market, market_id)
    
    await asyncio.to_thread(add_to_market_list, market_id, market_title, 'watching', base_price)
    
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Снять с наблюдения", callback_data=f"unlist_mkt_{market_id[:40]}")]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception:
        pass
    
    price_str = f" (база: {int(base_price * 100)}¢)" if base_price else ""
    await callback.answer(
        f"👁 Рынок добавлен в 'Следить'{price_str}. Уведомлю при скачке цены +50%.",
        show_alert=True
    )


@dp.callback_query(F.data.startswith("unlist_mkt_"))
async def callback_unlist_market(callback: CallbackQuery) -> None:
    """'Убрать' — удаляет рынок из обоих списков."""
    market_id = callback.data[len("unlist_mkt_"):]
    removed = await asyncio.to_thread(remove_from_market_list, market_id, None)
    
    # Восстанавливаем кнопки на сообщении если возможно
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Игнорировать", callback_data=f"ignore_mkt_{market_id[:40]}"),
            InlineKeyboardButton(text="👁 Следить", callback_data=f"watch_mkt_{market_id[:40]}"),
        ]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception:
        pass
    
    if removed > 0:
        await callback.answer("✅ Рынок удалён из списков. Будет анализироваться при следующем скане.", show_alert=True)
    else:
        await callback.answer("ℹ️ Рынок не найден в списках.")


@dp.callback_query(F.data.startswith("lists_remove_"))
async def callback_lists_remove(callback: CallbackQuery) -> None:
    """Удаляет рынок через кнопку в /lists."""
    # формат: lists_remove_{list_type}_{market_id_truncated}
    parts = callback.data.split("_", 3)  # [lists, remove, list_type, market_id]
    if len(parts) < 4:
        await callback.answer("Ошибка формата.")
        return
    list_type = parts[2]
    market_id = parts[3]
    await asyncio.to_thread(remove_from_market_list, market_id, list_type)
    await callback.answer("✅ Удалено.", show_alert=False)
    # Обновляем список
    await _send_lists_page(callback.message, edit=True)


def _extract_market_title_from_message(message) -> str:
    """Извлекает заголовок рынка из текста сообщения (первая строка после заголовка рынка)."""
    try:
        text = message.text or message.caption or ""
        # Берём первую непустую строку как название (макс 80 симв)
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and len(line) > 5:
                return line[:80]
    except Exception:
        pass
    return "(без названия)"


def _get_last_price_for_market(market_id: str) -> float:
    """Возвращает последнюю цену рынка из price_history или 0.5 если данных нет."""
    try:
        from agents.shared.python.db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT price FROM price_history WHERE market_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (market_id,)
            ).fetchone()
        return float(row['price']) if row else None
    except Exception:
        return None


async def _send_lists_page(message_or_target, edit: bool = False) -> None:
    """Формирует и отправляет/редактирует страницу /lists."""
    ignored = await asyncio.to_thread(get_market_list, 'ignored')
    watching = await asyncio.to_thread(get_market_list, 'watching')
    
    text = "📋 <b>Списки рынков</b>\n\n"
    
    rows = []
    buttons = []
    
    text += f"🚫 <b>Игнорируемые ({len(ignored)})</b>\n"
    if ignored:
        for entry in ignored[:15]:
            title = (entry.get('market_title') or entry['market_id'])[:60]
            title_safe = title.replace('<', '&lt;').replace('>', '&gt;')
            mid = entry['market_id'][:40]
            text += f"  • {title_safe}\n"
            buttons.append([
                InlineKeyboardButton(
                    text=f"✂️ {title[:30]}",
                    callback_data=f"lists_remove_ignored_{mid}"
                )
            ])
    else:
        text += "  <i>Пусто</i>\n"
    
    text += f"\n👁 <b>Слежу ({len(watching)})</b>\n"
    if watching:
        for entry in watching[:15]:
            title = (entry.get('market_title') or entry['market_id'])[:60]
            title_safe = title.replace('<', '&lt;').replace('>', '&gt;')
            mid = entry['market_id'][:40]
            bp = entry.get('base_price')
            bp_str = f" (base: {int(bp * 100)}¢)" if bp else ""
            text += f"  • {title_safe}{bp_str}\n"
            buttons.append([
                InlineKeyboardButton(
                    text=f"✂️ {title[:30]}",
                    callback_data=f"lists_remove_watching_{mid}"
                )
            ])
    else:
        text += "  <i>Пусто</i>\n"
    
    text += "\n<i>Кнопка ✂️ = удалить из списка</i>"
    
    if not buttons:
        buttons = [[InlineKeyboardButton(text="❌ Закрыть", callback_data="close_message")]]
    else:
        buttons.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="close_message")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if edit and isinstance(message_or_target, types.Message):
        try:
            await message_or_target.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
            return
        except Exception:
            pass
    
    if isinstance(message_or_target, types.Message):
        await message_or_target.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message_or_target.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        await message_or_target.answer()


@dp.message(Command("lists"))
async def command_lists_handler(message: types.Message) -> None:
    """Показывает списки Игнорировать и Следить."""
    await _send_lists_page(message)


@dp.message(F.text)
async def conversational_handler(message: types.Message) -> None:
    # Игнорируем команды
    if message.text.startswith("/"):
        return
        
    chat_id = message.chat.id
    user_text = message.text
    
    # Уведомляем пользователя, что бот печатает ответ
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Получаем историю чата (последние 15 сообщений для контекста)
    history = await asyncio.to_thread(get_chat_history, chat_id, 15)
    
    # Отправляем запрос к Gemini
    response_text = await asyncio.to_thread(ask_gemini, user_text, history)
    
    # Не сохраняем в историю ошибки (таймаут или сбой API)
    if not response_text.startswith("Ошибка"):
        # Сохраняем сообщение пользователя и ответ в базу
        await asyncio.to_thread(save_chat_message, chat_id, "user", user_text)
        await asyncio.to_thread(save_chat_message, chat_id, "model", response_text)
        
        # Очищаем старую историю (согласно MEMORY_POLICY не храним длинные логи)
        await asyncio.to_thread(cleanup_chat_history, chat_id, 20)
    
    # Отправляем ответ пользователю
    try:
        await message.answer(response_text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Ошибка при отправке сообщения в HTML: {e}. Пробуем отправить как обычный текст...")
        try:
            await message.answer(response_text, parse_mode=None)
        except Exception as e2:
            logger.error(f"Критическая ошибка при отправке сообщения в Telegram: {e2}")

async def main() -> None:
    from config import startup_check
    startup_check()
    print("🤖 Бот NEXUS запускается...")
    await set_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
