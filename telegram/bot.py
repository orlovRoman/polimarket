import asyncio
import logging
import os
import sqlite3
import subprocess
import requests
import json
from aiogram import Bot, Dispatcher, types, F
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
        BotCommand(command="stats", description="Статистика базы данных"),
        BotCommand(command="settings", description="Настройка лимитов запросов"),
        BotCommand(command="model", description="Выбор языковой модели"),
        BotCommand(command="logs", description="Просмотр последних логов"),
        BotCommand(command="cleanup", description="Очистить устаревшие сигналы"),
    ]
    await bot.set_my_commands(commands)

# Глобальный флаг для предотвращения одновременных запусков сканирования
is_scanning = False

def _is_stale_message(message: types.Message, max_age_seconds: int = 30) -> bool:
    """Проверяет, не устарело ли сообщение (защита от дублей после простоя бота)."""
    if message.date:
        try:
            now = datetime.now(message.date.tzinfo)
            return (now - message.date) > timedelta(seconds=max_age_seconds)
        except Exception:
            pass
    return False

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
    if _is_stale_message(message): return
    welcome_text = (
        f"Привет, <b>{message.from_user.full_name}</b>! 👋\n\n"
        f"Я <b>NEXUS</b> — терминал управления AI-командой Polymarket.\n\n"
        f"Моя задача — непрерывный мониторинг рынков и поиск возможностей.\n\n"
        f"Используй /help, чтобы увидеть, что я умею."
    )
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def command_help_handler(message: types.Message) -> None:
    if _is_stale_message(message): return
    help_text = (
        "📚 <b>Справочник команд NEXUS:</b>\n\n"
        "<b>Основные:</b>\n"
        "🚀 /scan — запустить поиск идей (выбор из 7 категорий)\n"
        "💡 /ideas — показать последние 5 активных сигналов\n"
        "⚙️ /status — детальный статус агентов и метрики памяти\n\n"
        "<b>Настройки:</b>\n"
        "🛠 /settings — лимит рынков + порог Edge (SCOUT)\n"
        "🧠 /model — выбрать языковую модель Gemini\n"
        "📊 /stats — общая статистика (рынки, сигналы, мнения)\n"
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
    if _is_stale_message(message): return
    from agents.shared.python.db import DB_PATH, get_connection, get_memory_stats
    
    # Получаем время последнего сканирования и лимит из БД
    last_scan_str = "Неизвестно"
    scan_limit = 10
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM memory WHERE key IN ('last_scan_time', 'scan_limit')")
            rows = cursor.fetchall()
            for row in rows:
                if row['key'] == 'last_scan_time':
                    last_scan_str = json.loads(row['value'])
                elif row['key'] == 'scan_limit':
                    scan_limit = json.loads(row['value'])
    except Exception:
        pass

    # Получаем метрики памяти
    stats = await asyncio.to_thread(get_memory_stats)

    status_text = (
        "📊 <b>Статус системы (24/7 Monitoring):</b>\n\n"
        f"● <b>Оркестратор (NEXUS):</b> 🟢 В сети\n"
        f"● <b>Агенты (SCOUT, SHADOW):</b> 🟢 Готовы\n"
        f"● <b>Планировщик:</b> 🟢 Активен (5 мин)\n"
        f"● <b>База данных:</b> {'🟢 OK' if DB_PATH.exists() else '🔴 Ошибка'}\n"
        f"● <b>Лимит запросов:</b> <code>{scan_limit} рынков/цикл</code>\n"
        f"● <b>Текущее действие:</b> {'🟡 Сканирование...' if is_scanning else '🟢 Ожидание'}\n\n"
        f"🧠 <b>Память:</b>\n"
        f"  Факты (Layer 1): {stats.get('facts', '?')}\n"
        f"  Рынков в БД: {stats.get('markets', '?')}\n"
        f"  Сигналов (активных): {stats.get('signals_pending', '?')}\n"
        f"  Сигналов (архив): {stats.get('signals_archived', '?')}\n"
        f"  Мнений агентов: {stats.get('opinions', '?')}\n"
        f"  Vault файлов: {stats.get('vault_files', '?')}\n"
        f"  Размер БД: {stats.get('db_size_kb', 0):.0f} KB\n\n"
        f"🕒 <b>Последнее авто-сканирование:</b>\n<code>{last_scan_str}</code>"
    )
    await message.answer(status_text)

@dp.message(Command("stats"))
async def command_stats_handler(message: types.Message) -> None:
    if _is_stale_message(message): return
    await message.answer(await asyncio.to_thread(get_db_stats))

@dp.message(Command("settings"))
async def command_settings_handler(message: types.Message) -> None:
    from agents.shared.python.db import get_memory
    current_edge = int((get_memory("min_edge") or 0.10) * 100)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Лимит рынков (scan_limit)", callback_data="settings_limits")],
        [InlineKeyboardButton(text=f"🎯 Edge порог: {current_edge}%", callback_data="settings_edge")],
    ])
    await message.answer("⚙️ <b>Настройки системы:</b>\n\nВыберите параметр для настройки:", reply_markup=keyboard)

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
        [InlineKeyboardButton(text="Gemini 2.5 Pro (Рекомендуется)", callback_data="setmodel_gemini-2.5-pro")],
        [InlineKeyboardButton(text="Gemini 2.5 Flash (Быстрая)", callback_data="setmodel_gemini-2.5-flash")],
        [InlineKeyboardButton(text="Gemini 1.5 Pro (Старая)", callback_data="setmodel_gemini-1.5-pro-latest")]
    ])
    await message.answer("🧠 <b>Выбор языковой модели:</b>\n\nТекущая модель влияет на ответы агентов. Выберите предпочитаемую версию Gemini:", reply_markup=keyboard)

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
        # Читаем последние 10 строк
        logs = subprocess.check_output(["tail", "-n", "10", str(LOG_PATH)]).decode("utf-8")
        # Экранируем спецсимволы для HTML
        safe_logs = logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        await message.answer(f"📜 <b>Последние логи:</b>\n<pre>{safe_logs}</pre>")
    except Exception as e:
        await message.answer(f"Ошибка чтения логов: {e}")

@dp.message(Command("cleanup"))
async def command_cleanup_handler(message: types.Message) -> None:
    """Очищает устаревшие сигналы (2025, истёкшие рынки)."""
    if _is_stale_message(message): return
    count = await asyncio.to_thread(cleanup_stale_signals)
    await message.answer(f"🧹 Очистка завершена. Архивировано устаревших сигналов: {count}")

@dp.message(Command("scan"))
async def command_scan_handler(message: types.Message) -> None:
    if _is_stale_message(message): return
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
        [InlineKeyboardButton(text="🎬 Культура", callback_data="scan_culture"),
         InlineKeyboardButton(text="💼 Бизнес", callback_data="scan_business")]
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
    is_scanning = True
    status_msg = callback.message
    
    log_lines = []
    def log_callback(text):
        log_lines.append(text)

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
            if log_lines and is_scanning:
                current_text = "\\n".join(log_lines[-20:])
                if current_text != last_text:
                    try:
                        await status_msg.edit_text(f"<b>Процесс обсуждения (Категория: {cat_name}):</b>\\n<pre>{current_text}</pre>", parse_mode="HTML")
                        last_text = current_text
                    except Exception:
                        pass

    updater_task = asyncio.create_task(update_message())
    
    try:
        import sys, os; sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); from run_team import run_team_discussion
        await asyncio.to_thread(run_team_discussion, log_callback, summary_callback, category_param)
        if log_lines:
            await status_msg.edit_text(f"<b>Процесс обсуждения завершен (Категория: {cat_name}):</b>\\n<pre>{chr(10).join(log_lines[-20:])}</pre>", parse_mode="HTML")
        await callback.message.answer("✅ Сканирование завершено! Используйте /ideas чтобы увидеть результат.")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка во время сканирования: {e}")
    finally:
        is_scanning = False
        # Wait a bit to ensure the queue is empty before cancelling
        await asyncio.sleep(2.5)
        updater_task.cancel()
    
    await callback.answer()

@dp.message(Command("ideas"))
async def command_ideas_handler(message: types.Message) -> None:
    if _is_stale_message(message): return
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
        await message.answer(response_text)
    except Exception as e:
        print(f"Ошибка при отправке сообщения в Telegram: {e}")

async def main() -> None:
    print("🤖 Бот NEXUS запускается...")
    await set_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
