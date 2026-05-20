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
from datetime import datetime

# Импортируем функции БД
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.shared.python.db import save_chat_message, get_chat_history, init_db, get_db_stats, get_signals

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
LOG_PATH = Path(__file__).parent.parent / "logs" / "main.log"
ORCHESTRATOR_GEMINI_MD = Path(__file__).parent.parent / "agents" / "orchestrator" / "GEMINI.md"

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env файле!")

# Инициализируем БД при запуске
init_db()

# Включаем логирование
logging.basicConfig(level=logging.INFO)

# Инициализируем бота и диспетчер
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить приветствие"),
        BotCommand(command="help", description="Справочник всех команд"),
        BotCommand(command="status", description="Статус системы и агентов"),
        BotCommand(command="scan", description="Запустить ручной поиск идей"),
        BotCommand(command="ideas", description="Торговые сигналы"),
        BotCommand(command="stats", description="Статистика базы данных"),
        BotCommand(command="logs", description="Просмотр логов"),
    ]
    await bot.set_my_commands(commands)

# Глобальная переменная для отслеживания состояния сканирования
is_scanning = False

def get_nexus_system_prompt():
    prompt = "Ты — NEXUS, главный ИИ-координатор команды агентов (SCOUT, SHADOW, HERALD), анализирующих рынки Polymarket. Твоя цель — общаться с пользователем в живом диалоге, помогать ему управлять системой и давать советы. Отвечай кратко, профессионально и по делу."
    if ORCHESTRATOR_GEMINI_MD.exists():
        try:
            with open(ORCHESTRATOR_GEMINI_MD, "r") as f:
                prompt += "\n\nТвои инструкции:\n" + f.read()
        except Exception:
            pass
    return prompt

def ask_gemini(text: str, history: list = None) -> str:
    if not GOOGLE_API_KEY:
        return "Ошибка: GOOGLE_API_KEY не настроен."
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={GOOGLE_API_KEY}"
    
    contents = history if history else []
    contents.append({"role": "user", "parts": [{"text": text}]})
    
    payload = {
        "contents": contents,
        "tools": [{"google_search_retrieval": {}}],
        "systemInstruction": {"role": "system", "parts": [{"text": get_nexus_system_prompt()}]}
    }
    
    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Ошибка API: {response.status_code}\n{response.text}"
    except Exception as e:
        return f"Ошибка соединения: {e}"

@dp.message(CommandStart())
async def command_start_handler(message: types.Message) -> None:
    welcome_text = (
        f"Привет, <b>{message.from_user.full_name}</b>! 👋\n\n"
        f"Я <b>NEXUS</b> — терминал управления AI-командой Polymarket.\n\n"
        f"Я работаю 24/7, сканирую рынки и ищу недооцененные события.\n\n"
        f"Используй /help для просмотра всех доступных команд."
    )
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def command_help_handler(message: types.Message) -> None:
    help_text = (
        "📚 <b>Справочник команд NEXUS:</b>\n\n"
        "<b>Основные:</b>\n"
        "🚀 /scan — запустить принудительный поиск идей (выбор категории)\n"
        "💡 /ideas — показать последние 5 активных сигналов\n"
        "⚙️ /status — детальный статус агентов и планировщика\n\n"
        "<b>Аналитика и БД:</b>\n"
        "📊 /stats — общая статистика (рынки, сигналы, мнения)\n"
        "📜 /logs — последние 10 строк системного лога\n\n"
        "<b>Информация:</b>\n"
        "❓ /help — это сообщение\n"
        "👋 /start — перезапустить приветствие\n\n"
        "<i>Ты также можешь просто писать мне вопросы в чат — я отвечу, используя контекст нашей команды.</i>"
    )
    await message.answer(help_text)

@dp.message(Command("status"))
async def command_status_handler(message: types.Message) -> None:
    from agents.shared.python.db import DB_PATH, get_connection
    
    # Пытаемся получить время последнего сканирования из БД
    last_scan_str = "Неизвестно"
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM memory WHERE key = 'last_scan_time'")
            row = cursor.fetchone()
            if row:
                last_scan_str = json.loads(row['value'])
    except Exception:
        pass

    status_text = (
        "📊 <b>Статус системы (24/7 Monitoring):</b>\n\n"
        f"● <b>Оркестратор (NEXUS):</b> 🟢 В сети\n"
        f"● <b>Агенты (SCOUT, SHADOW):</b> 🟢 Готовы\n"
        f"● <b>Планировщик:</b> 🟢 Активен (30 мин)\n"
        f"● <b>База данных:</b> {'🟢 OK' if DB_PATH.exists() else '🔴 Ошибка'}\n"
        f"● <b>Текущее действие:</b> {'🟡 Сканирование...' if is_scanning else '🟢 Ожидание'}\n\n"
        f"🕒 <b>Последнее авто-сканирование:</b>\n<code>{last_scan_str}</code>"
    )
    await message.answer(status_text)

@dp.message(Command("stats"))
async def command_stats_handler(message: types.Message) -> None:
    await message.answer(await asyncio.to_thread(get_db_stats))

@dp.message(Command("logs"))
async def command_logs_handler(message: types.Message) -> None:
    if not LOG_PATH.exists():
        await message.answer("Лог-файл еще не создан.")
        return
    
    try:
        logs = subprocess.check_output(["tail", "-n", "10", str(LOG_PATH)]).decode("utf-8")
        await message.answer(f"📜 <b>Последние логи:</b>\n<pre>{logs}</pre>")
    except Exception as e:
        await message.answer(f"Ошибка чтения логов: {e}")

@dp.message(Command("scan"))
async def command_scan_handler(message: types.Message) -> None:
    global is_scanning
    if is_scanning:
        await message.answer("⚠️ Сканирование уже запущено. Пожалуйста, подождите.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Всё (Топ объема)", callback_data="scan_all")],
        [InlineKeyboardButton(text="Политика", callback_data="scan_politics")],
        [InlineKeyboardButton(text="Спорт", callback_data="scan_sports")],
        [InlineKeyboardButton(text="Крипта", callback_data="scan_crypto")]
    ])
    await message.answer("Выберите категорию для сканирования:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("scan_"))
async def callback_scan_handler(callback: CallbackQuery) -> None:
    global is_scanning
    if is_scanning:
        await callback.answer("⚠️ Сканирование уже запущено. Пожалуйста, подождите.", show_alert=True)
        return

    category = callback.data.split("_")[1]
    if category == "all":
        category_param = None
        cat_name = "Все рынки"
    else:
        category_param = category
        cat_map = {"politics": "Политика", "sports": "Спорт", "crypto": "Крипта"}
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
    
    # Уведомляем пользователя, что бот печатает ответ
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Получаем историю чата (последние 15 сообщений для контекста)
    history = await asyncio.to_thread(get_chat_history, chat_id, 15)
    
    # Отправляем запрос к Gemini
    response_text = await asyncio.to_thread(ask_gemini, message.text, history)
    
    # Сохраняем сообщение пользователя и ответ в базу
    await asyncio.to_thread(save_chat_message, chat_id, "user", message.text)
    await asyncio.to_thread(save_chat_message, chat_id, "model", response_text)
    
    # Отправляем ответ пользователю
    try:
        await message.answer(response_text)
    except Exception as e:
        await message.answer(f"Ошибка при отправке сообщения: {e}")

async def main() -> None:
    print("🤖 Бот NEXUS запускается...")
    await set_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
