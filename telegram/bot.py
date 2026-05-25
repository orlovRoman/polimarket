import asyncio
import logging
import os
import sqlite3
import subprocess
import requests
import json
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timedelta

# Импортируем функции БД
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.shared.python.db import save_chat_message, get_chat_history, init_db, get_db_stats, get_signals, cleanup_chat_history, cleanup_stale_signals
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

# Инициализируем NexusAgent
nexus_agent = NexusAgent()

# Инициализируем бота и диспетчер событий aiogram
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ID чата, авторизованного для управления ботом
AUTHORIZED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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
        BotCommand(command="correlations", description="Корреляции между рынками"),
        BotCommand(command="stats", description="Статистика базы данных"),
        BotCommand(command="settings", description="Настройка лимитов запросов"),
        BotCommand(command="model", description="Выбор языковой модели"),
        BotCommand(command="logs", description="Просмотр последних логов"),
        BotCommand(command="cleanup", description="Очистить устаревшие сигналы"),
        BotCommand(command="restart", description="Перезапуск бота"),
    ]
    await bot.set_my_commands(commands)

# Глобальный флаг для предотвращения одновременных запусков сканирования
is_scanning = False

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
        if AUTHORIZED_CHAT_ID:
            chat_id = str(event.chat.id) if hasattr(event, "chat") and event.chat else None
            if not chat_id and hasattr(event, "message") and event.message:
                chat_id = str(event.message.chat.id)
            if chat_id and chat_id != AUTHORIZED_CHAT_ID:
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
        return nexus_agent.process_prompt(text, history)
    except Exception as e:
        return f"Ошибка при обращении к NEXUS: {e}"

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
        "⚙️ /status — детальный статус агентов и метрики памяти\n"
        "📊 /audit — аудит воронки идей (отказы SHADOW)\n\n"
        "<b>Настройки:</b>\n"
        "🛠 /settings — лимит рынков + порог Edge (SCOUT)\n"
        "🧠 /model — выбрать языковую модель Gemini\n"
        "📈 /stats — общая статистика (рынки, сигналы)\n"
        "🧹 /cleanup — архивировать устаревшие сигналы\n"
        "📜 /logs — последние 10 строк системного лога\n\n"
        "<b>Информация:</b>\n"
        "❓ /help — это сообщение\n"
        "👋 /start — перезапустить приветствие\n\n"
        "<i>Ты также можешь просто писать мне вопросы в чат — я отвечу, используя контекст нашей команды.</i>"
    )
    await message.answer(help_text)

@dp.message(Command("status"))
async def command_status_handler(message: types.Message) -> None:
    from agents.shared.python.db import DB_PATH, get_connection, get_memory_stats, get_token_usage_last_24h, get_memory, get_agent_model
    
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
    
    # Загружаем используемые модели и суточный расход токенов для каждого агента
    nexus_model = get_memory("selected_model", "gemini-2.5-flash")
    scout_model = await asyncio.to_thread(get_agent_model, "SCOUT", "gemini-2.5-flash")
    swing_model = await asyncio.to_thread(get_agent_model, "SWING", "gemini-2.5-flash")
    shadow_model = await asyncio.to_thread(get_agent_model, "SHADOW", "gemini-2.5-flash")
    
    scout_tokens = await asyncio.to_thread(get_token_usage_last_24h, "SCOUT")
    swing_tokens = await asyncio.to_thread(get_token_usage_last_24h, "SWING")
    shadow_tokens = await asyncio.to_thread(get_token_usage_last_24h, "SHADOW")
    
    def format_tokens(t):
        tot = t.get('total_tokens', 0)
        inp = t.get('input_tokens', 0)
        out = t.get('output_tokens', 0)
        if tot == 0:
            return "<code>0 токенов</code>"
        return f"<code>{tot:,}</code> ({inp:,} in + {out:,} out)"

    # Реальный статус сканирования
    import time
    from config import LOCK_FILE, LOCK_TIMEOUT_SEC
    is_scanning_real = False
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                data = f.read().strip().split(",")
                if len(data) == 2:
                    if time.time() - float(data[0]) < LOCK_TIMEOUT_SEC:
                        is_scanning_real = True
        except Exception:
            pass

    status_text = (
        "📊 <b>Статус системы (24/7 Monitoring):</b>\n\n"
        f"● <b>Оркестратор NEXUS:</b> 🟢 Активен (Model: <code>{nexus_model}</code>)\n"
        f"● <b>Агенты (SCOUT, SWING, SHADOW):</b> 🟢 Готовы\n"
        f"● <b>Telegram Слушатель:</b> 🟢 Активен (5 мин)\n"
        f"● <b>Trend Hunter:</b> {'🟢 Активен (2 ч)' if trend_hunter_enabled else '🔴 Отключен'}\n"
        f"● <b>Тренды-оповещения:</b> {'🟢 Включены' if trend_hunter_alerts else '🔴 Отключены'}\n"
        f"● <b>База данных:</b> {'🟢 OK' if DB_PATH.exists() else '🔴 Ошибка'}\n"
        f"● <b>Лимит запросов:</b> <code>{scan_limit} рынков/цикл</code>\n"
        f"● <b>Текущее действие:</b> {'🟡 Сканирование...' if is_scanning_real else '🟢 Ожидание'}\n\n"
        f"🤖 <b>AI Агенты и токен-баланс (24ч):</b>\n"
        f"  ● <b>SCOUT:</b> <code>{scout_model}</code> | {format_tokens(scout_tokens)}\n"
        f"  ● <b>SWING:</b> <code>{swing_model}</code> | {format_tokens(swing_tokens)}\n"
        f"  ● <b>SHADOW:</b> <code>{shadow_model}</code> | {format_tokens(shadow_tokens)}\n\n"
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

    await message.answer(status_text)

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

@dp.message(Command("settings"))
async def command_settings_handler(message: types.Message) -> None:
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
        [InlineKeyboardButton(text="🔎 Настройки Trend Hunter", callback_data="settings_trend_hunter")],
    ])
    await message.answer("⚙️ <b>Настройки системы:</b>\n\nВыберите параметр для настройки:", reply_markup=keyboard)

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
    asyncio.create_task(asyncio.to_thread(run_trend_hunter))

@dp.callback_query(F.data == "back_to_settings")
async def callback_back_to_settings(callback: CallbackQuery) -> None:
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
        [InlineKeyboardButton(text="🔎 Настройки Trend Hunter", callback_data="settings_trend_hunter")],
    ])
    await callback.message.edit_text("⚙️ <b>Настройки системы:</b>\n\nВыберите параметр для настройки:", reply_markup=keyboard)
    await callback.answer()

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
async def callback_setrag_handler(callback: CallbackQuery) -> None:
    level = int(callback.data.split("_")[1])
    from agents.shared.python.db import save_memory
    await asyncio.to_thread(save_memory, "rag_level", level)
    
    rag_labels = {1: "Быстрый (L1)", 2: "Стандарт (L2)", 3: "Глубокий (L3)"}
    level_text = rag_labels.get(level, "Стандарт (L2)")
    
    await callback.message.edit_text(
        f"✅ <b>Глубина RAG обновлена!</b>\n"
        f"Теперь используется режим: <b>{level_text}</b>"
    )
    await callback.answer()

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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Gemini 2.5 Flash (Рекомендуется)", callback_data="setmodel_gemini-2.5-flash")],
        [InlineKeyboardButton(text="🚀 Gemini 3.5 Flash (Новейшая)", callback_data="setmodel_gemini-3.5-flash")],
        [InlineKeyboardButton(text="🧠 Gemini 2.5 Pro (Дорогая, мощная)", callback_data="setmodel_gemini-2.5-pro")],
    ])
    await message.answer("🧠 <b>Выбор языковой модели:</b>\n\nВлияет на ответы NEXUS и скрининг рынков. Агенты SCOUT/SHADOW используют Flash по умолчанию.", reply_markup=keyboard)

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
        with open(LOG_PATH, "r", encoding="utf-8") as f:
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
        
    await message.answer("🔄 <b>Перезапуск бота...</b>\nПроцесс будет завершен, операционная система автоматически поднимет его заново через пару секунд.", parse_mode="HTML")
    logging.warning("Получена команда /restart. Завершаю процесс (os._exit(0))...")
    
    # Даем Telegram время на отправку сообщения перед убийством процесса
    await asyncio.sleep(1)
    os._exit(0)

@dp.message(Command("cleanup"))
async def command_cleanup_handler(message: types.Message) -> None:
    """Очищает устаревшие сигналы (2025, истёкшие рынки)."""
    count = await asyncio.to_thread(cleanup_stale_signals)
    await message.answer(f"🧹 Очистка завершена. Архивировано устаревших сигналов: {count}")

@dp.message(Command("correlations"))
async def command_correlations_handler(message: types.Message) -> None:
    """Показывает найденные корреляции между рынками."""
    from agents.shared.python.db import get_connection
    
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT correlation_type, title_a, title_b, description, confidence, detected_at
                FROM correlations
                ORDER BY detected_at DESC
                LIMIT 10
            """)
            rows = cursor.fetchall()
        
        if not rows:
            await message.answer("🔗 Корреляции пока не обнаружены. Запустите /scan для скрининга.")
            return
        
        type_icons = {
            'causal': '🔄 ПРИЧИННАЯ',
            'inverse': '↕️ ОБРАТНАЯ',
            'arbitrage': '⚡ АРБИТРАЖ',
            'thematic': '🔗 ТЕМАТИЧЕСКАЯ'
        }
        
        response = f"🔗 <b>Корреляции между рынками (Top-10):</b>\n\n"
        for i, row in enumerate(rows, 1):
            corr_type = type_icons.get(row['correlation_type'], row['correlation_type'])
            conf = row['confidence'] or 0
            response += (
                f"<b>{i}. {corr_type}</b> ({conf:.0%})\n"
                f"  📍 {row['title_a']}\n"
                f"  📍 {row['title_b']}\n"
                f"  → <i>{row['description']}</i>\n\n"
            )
        
        await message.answer(response, disable_web_page_preview=True)
    except Exception as e:
        await message.answer(f"Ошибка при получении корреляций: {e}")

@dp.message(Command("scan"))
async def command_scan_handler(message: types.Message) -> None:
    global is_scanning
    if is_scanning:
        await message.answer("⚠️ Сканирование уже запущено. Пожалуйста, подождите.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Все (авто-микс)", callback_data="scan_all")],
        [InlineKeyboardButton(text="🏛 Политика", callback_data="scan_politics"),
         InlineKeyboardButton(text="₿ Крипто", callback_data="scan_crypto")],
        [InlineKeyboardButton(text="⚽ Спорт", callback_data="scan_sports"),
         InlineKeyboardButton(text="🔬 Наука", callback_data="scan_science")],
        [InlineKeyboardButton(text="💼 Бизнес", callback_data="scan_business")]
    ])
    await message.answer("🔍 <b>Выберите категорию для сканирования:</b>", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("scan_"))
async def callback_scan_handler(callback: CallbackQuery) -> None:
    global is_scanning
    if is_scanning:
        await callback.answer("⚠️ Сканирование уже запущено. Пожалуйста, подождите.", show_alert=True)
        return

    category = callback.data.split("_")[1]
    if category == "all":
        category_param = None
        cat_name = "Все рынки (авто-микс)"
    else:
        category_param = category
        cat_map = {
            "politics": "🏛 Политика", "crypto": "₿ Крипто",
            "sports": "⚽ Спорт", "science": "🔬 Наука",
            "culture": "🎬 Культура", "business": "💼 Бизнес"
        }
        cat_name = cat_map.get(category, category)

    await callback.message.edit_text(f"🚀 Запускаю полный цикл анализа (Категория: {cat_name})...")
    await callback.answer("🔄 Сканирование запущено...")
    is_scanning = True
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
    def summary_callback(text):
        summaries_queue.append(text)

    async def update_message():
        last_text = ""
        while is_scanning or summaries_queue:
            await asyncio.sleep(2)
            # Send all pending summaries
            while summaries_queue:
                summary = summaries_queue.pop(0)
                try:
                    await callback.message.answer(summary, disable_web_page_preview=True)
                except Exception as e:
                    print(f"Ошибка отправки summary: {e}")
            
            # Update log status
            if current_state and is_scanning:
                new_html = render_dashboard(current_state)
                if new_html != last_text:
                    try:
                        await status_msg.edit_text(new_html, parse_mode="HTML", disable_web_page_preview=True)
                        last_text = new_html
                    except Exception:
                        pass

    updater_task = asyncio.create_task(update_message())
    
    try:
        import sys, os; sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); from run_team import run_team_discussion
        await asyncio.to_thread(run_team_discussion, log_callback, summary_callback, category_param, None, state_callback)
        if current_state:
            final_html = render_dashboard(current_state)
            await status_msg.edit_text(final_html + "\n\n<b>✅ ПРОЦЕСС ЗАВЕРШЕН</b>", parse_mode="HTML", disable_web_page_preview=True)
        await callback.message.answer("✅ Сканирование завершено! Используйте /ideas чтобы увидеть результат.")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка во время сканирования: {e}")
    finally:
        is_scanning = False
        # Wait a bit to ensure the queue is empty before cancelling
        await asyncio.sleep(2.5)
        updater_task.cancel()


@dp.message(Command("ideas"))
async def command_ideas_handler(message: types.Message) -> None:
    signals = await asyncio.to_thread(get_signals)
    if not signals:
        await message.answer("Пока нет новых идей. Запустите /scan.")
        return

    response = "🚀 <b>Торговые сигналы (Top 5):</b>\n\n"
    for s in signals:
        edge_pct = (s['edge'] or 0) * 100
        response += (
            f"📍 <b>{s['title']}</b>\n"
            f"💰 Цена: {s['market_price']} | 📈 Edge: <b>+{edge_pct:.1f}%</b>\n"
            f"🎯 Уверенность: {s['confidence']}\n"
            f"📝 {s['summary']}\n"
            f"🔗 <a href='{s['url']}'>Открыть рынок</a>\n\n"
        )
    await message.answer(response, disable_web_page_preview=True)

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
        print(f"Ошибка при отправке сообщения в HTML: {e}. Пробуем отправить как обычный текст...")
        try:
            await message.answer(response_text, parse_mode=None)
        except Exception as e2:
            print(f"Критическая ошибка при отправке сообщения в Telegram: {e2}")

async def main() -> None:
    print("🤖 Бот NEXUS запускается...")
    await set_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
