from __future__ import annotations
import asyncio
import logging
logger = logging.getLogger("NexusPolyBot")
import os
import sqlite3
import threading
import subprocess
import requests
import json
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand, LinkPreviewOptions, ReplyKeyboardMarkup, KeyboardButton
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
    get_market_list, is_alert_already_sent, archive_signal_by_id
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
_add_idea_lock = asyncio.Lock()
_manual_signal_lock = threading.Lock()
_callback_dedup_lock = asyncio.Lock()

from collections import deque
_processed_message_ids: deque[tuple[int, int]] = deque(maxlen=500)
_processed_callback_ids: deque[str] = deque(maxlen=200)

def get_core_engine():
    """Возвращает единственный экземпляр CoreEngine (синглтон)."""
    from core.singleton import get_core_engine as _get_shared_engine
    return _get_shared_engine()


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
    log.propagate = False
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
        BotCommand(command="monitor", description="Управление мониторингом (Вкл/Выкл)"),
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
        BotCommand(command="eval", description="Запуск оценки торговых стратегий"),
        BotCommand(command="eval_status", description="Просмотр текущих порогов"),
        BotCommand(command="eval_history", description="История калибровок стратегии"),
        BotCommand(command="eval_apply", description="Применить калибровочное предложение"),
        BotCommand(command="eval_rollback", description="Откатить калибровочное изменение"),
        BotCommand(command="gate_stats", description="Статистика On-chain Gatekeeper (экономия)"),
        BotCommand(command="penny", description="Меню Penny Stocks (дешевые рынки)"),
        BotCommand(command="compound", description="Favourite Compounding (≥95¢)"),
    ]
    await bot.set_my_commands(commands)

# Глобальный лок для предотвращения одновременных запусков сканирования
_scan_lock = asyncio.Lock()
_penny_scan_lock = asyncio.Lock()
_favourite_compound_lock = asyncio.Lock()

# ── Управление мониторингом (через /monitor) ──────────────────────────────────
_monitoring_task: asyncio.Task | None = None
_monitoring_stop_event: asyncio.Event | None = None
_scheduler = None          # Передаётся из main.py через set_scheduler()
_auto_schedule_enabled = False  # По умолчанию — холодный старт (выкл)
_SCHEDULE_JOB_ID = "scheduled_market_scan"

def set_scheduler(scheduler) -> None:
    """Передаёт ссылку на AsyncIOScheduler из main.py для управления авто-расписанием."""
    global _scheduler
    _scheduler = scheduler

def _is_monitoring_active() -> bool:
    """Возвращает True, если фоновый цикл мониторинга активен."""
    return _monitoring_task is not None and not _monitoring_task.done()

async def continuous_monitoring_loop() -> None:
    """Непрерывный цикл сканирования рынков (запускается через /monitor).
    Интервал: 15 минут между итерациями. Останавливается по _monitoring_stop_event."""
    global _monitoring_task, _monitoring_stop_event
    logger.info("▶️ continuous_monitoring_loop запущен")
    try:
        # Импортируем здесь, чтобы избежать циклических импортов на старте бота
        from main import scheduled_job
        while True:
            if _monitoring_stop_event and _monitoring_stop_event.is_set():
                logger.info("⏹ Мониторинг остановлен по запросу пользователя")
                break
            try:
                await scheduled_job()
            except Exception as e:
                logger.error(f"[Monitor] Ошибка в итерации: {e}", exc_info=True)
            # Ждём 15 минут, но проверяем stop_event каждые 5 сек
            for _ in range(180):  # 180 * 5 сек = 15 минут
                if _monitoring_stop_event and _monitoring_stop_event.is_set():
                    break
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("⏹ Мониторинг отменён (CancelledError)")
    finally:
        _monitoring_task = None
        _monitoring_stop_event = None
        logger.info("▶️ continuous_monitoring_loop завершён")

class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Stale check for messages
        if isinstance(event, types.Message):
            if event.date:
                try:
                    now = datetime.now(event.date.tzinfo)
                    if (now - event.date) > timedelta(seconds=30):
                        return
                except Exception:
                    pass

        # Stale check for CallbackQuery — игнорируем кнопки из сообщений старше 10 минут
        if isinstance(event, types.CallbackQuery) and event.message:
            # ignore_mkt_ пропускает только stale-check (UX),
            # но авторизационная проверка ниже всё равно применяется
            is_ignore_click = event.data and event.data.startswith("ignore_mkt_")
            if not is_ignore_click:
                msg_date = event.message.date
                if msg_date:
                    try:
                        now = datetime.now(msg_date.tzinfo)
                        if (now - msg_date) > timedelta(minutes=10):
                            await event.answer("⚠️ Сессия устарела. Повторите команду заново.", show_alert=True)
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
        # $0.15 / 1M input, $0.60 / 1M output
        return (input_tokens * 0.15 / 1_000_000) + (output_tokens * 0.60 / 1_000_000)
        
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
    Клавиатура с кнопками 'Игнорировать', 'Следить' и 'В идеи'.
    market_id обрезается до 40 символов для соблюдения лимита callback_data = 64 байта aiogram.
    """
    mid = market_id[:40]  # UUID = 36 символов, вписывается с префиксами 11 симв (ignore_mkt_ / watch_mkt_)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Игнорировать", callback_data=f"ignore_mkt_{mid}"),
            InlineKeyboardButton(text="👁 Следить", callback_data=f"watch_mkt_{mid}"),
            InlineKeyboardButton(text="📥 В идеи", callback_data=f"add_idea_{mid}"),
        ]
    ])


async def send_or_edit(message_or_callback, text: str, keyboard: InlineKeyboardMarkup = None) -> None:
    """Вспомогательная функция для отправки нового или редактирования существующего сообщения."""
    from aiogram.exceptions import TelegramBadRequest
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    else:
        try:
            await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
        await message_or_callback.answer()

def build_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Строит постоянную Reply Keyboard с основными кнопками меню."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚦 Мониторинг"),
                KeyboardButton(text="🚀 Сканировать"),
            ],
            [
                KeyboardButton(text="🪙 Penny Stocks"),
                KeyboardButton(text="💰 Compound"),
            ],
            [
                KeyboardButton(text="💡 Идеи"),
                KeyboardButton(text="📚 Справка"),
            ]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard

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
    await message.answer(welcome_text, reply_markup=build_main_reply_keyboard())

@dp.message(Command("help"))
async def command_help_handler(message: types.Message) -> None:
    help_text = (
        "📚 <b>Справочник команд NEXUS:</b>\n\n"
        "<b>Основные:</b>\n"
        "🚦 /monitor — управление мониторингом (Вкл/Выкл + авто-расписание)\n"
        "🚀 /scan — запустить поиск идей (выбор из 11 категорий)\n"
        "💡 /ideas — показать последние 5 активных сигналов\n"
        "⚙️ /status — детальный статус агентов и метрики (в т.ч. Точность SCOUT*)\n"
        "📊 /audit — аудит воронки идей (отказы SHADOW)\n"
        "🛡 /gate_stats — статистика On-chain Gatekeeper (экономия)\n\n"
        "<b>Настройки:</b>\n"
        "🛠 /settings — лимит рынков + порог Edge (SCOUT)\n"
        "🧠 /model — выбрать языковую модель Gemini\n"
        "📈 /stats — общая статистика (рынки, сигналы)\n"
        "🧹 /cleanup — архивировать устаревшие сигналы\n"
        "📜 /logs — последние 10 строк системного лога\n\n"
        "<b>Калибровка и Оценка (Evaluation):</b>\n"
        "📊 /eval — запустить оценку торговых стратегий за 30 дней вручную\n"
        "⚙️ /eval_status — показать текущие торговые пороги систем\n"
        "📜 /eval_history &lt;strategy&gt; — история изменений порогов для стратегии\n"
        "✅ /eval_apply &lt;id&gt; — применить калибровочное предложение вручную\n"
        "⏪ /eval_rollback &lt;id&gt; — откатить изменение параметров к предыдущему значению\n\n"
        "❓ /help — это сообщение\n"
        "👋 /start — перезапустить приветствие\n\n"
        "<b>Экспериментальные функции:</b>\n"
        "⚖️ /arbitrage — кросс-платформенный арбитраж (Polymarket ↔ Kalshi)\n"
        "🔄 /synthetic — внутрирыночный арбитраж (синтетические коридоры Polymarket)\n"
        "🪙 /penny — Меню Penny Stocks (дешевые рынки)\n"
        "💰 /compound — Favourite Compounding (≥95¢)\n\n"
        "<i>*Точность SCOUT в меню /status показывает % успешных сигналов. Она 'накапливается', пока рынки, по которым бот дал сигнал, физически не закроются на Polymarket, чтобы сверить прогноз с реальностью.</i>\n\n"
        "<i>Ты также можешь просто писать мне вопросы в чат — я отвечу, используя контекст нашей команды.</i>"
    )
    await message.answer(help_text, reply_markup=build_main_reply_keyboard())

# Обработчики для текстовых кнопок Reply Keyboard
@dp.message(F.text == "🚦 Мониторинг")
async def reply_monitor(message: types.Message):
    await command_monitor_handler(message)

@dp.message(F.text == "🚀 Сканировать")
async def reply_scan(message: types.Message):
    await command_scan_handler(message)

@dp.message(F.text == "🪙 Penny Stocks")
async def reply_penny(message: types.Message):
    await command_penny_handler(message)

@dp.message(F.text == "💰 Compound")
async def reply_compound(message: types.Message):
    await cmd_compound(message)

@dp.message(F.text == "💡 Идеи")
async def reply_ideas(message: types.Message):
    await command_ideas_handler(message)

@dp.message(F.text == "📚 Справка")
async def reply_help(message: types.Message):
    await command_help_handler(message)


def build_monitor_keyboard() -> InlineKeyboardMarkup:
    """Строит клавиатуру панели мониторинга с актуальными статусами."""
    monitoring_active = _is_monitoring_active()
    schedule_on = _auto_schedule_enabled
    rows = []
    if monitoring_active:
        rows.append([InlineKeyboardButton(text="⏹ Остановить мониторинг", callback_data="monitor_stop")])
    else:
        rows.append([InlineKeyboardButton(text="▶️ Запустить мониторинг", callback_data="monitor_start")])
    schedule_label = f"⏰ Авто-расписание: {'🟢 Вкл' if schedule_on else '🔴 Выкл'}"
    rows.append([InlineKeyboardButton(text=schedule_label, callback_data="monitor_schedule_toggle")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить статус", callback_data="monitor_refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_monitor_text() -> str:
    """Формирует текст панели мониторинга."""
    monitoring_active = _is_monitoring_active()
    schedule_on = _auto_schedule_enabled
    monitoring_status = "🟢 <b>Активен</b> (цикл каждые 15 мин)" if monitoring_active else "🔴 <b>Остановлен</b>"
    schedule_status = "🟢 <b>Включено</b> (каждые 15 мин)" if schedule_on else "🔴 <b>Выключено</b>"
    return (
        "🚦 <b>Управление мониторингом NEXUS</b>\n\n"
        f"● Мониторинг: {monitoring_status}\n"
        f"● Авто-расписание: {schedule_status}\n\n"
        "<i>▶️ <b>Запустить</b> — включает непрерывный цикл сканирования.\n"
        "⏰ <b>Авто-расписание</b> — apscheduler запускает сканирование каждые 15 мин независимо.\n"
        "После рестарта бота оба режима всегда выключены (холодный старт).</i>"
    )


@dp.message(Command("monitor"))
async def command_monitor_handler(message: types.Message) -> None:
    """Панель управления мониторингом: Вкл/Выкл, Авто-расписание."""
    await message.answer(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "monitor_refresh")
async def callback_monitor_refresh(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_text(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")
    except Exception:
        pass  # «message not modified» или аналогичная ошибка разметки
    try:
        await callback.answer("Обновлено")
    except Exception:
        pass


@dp.callback_query(F.data == "monitor_start")
async def callback_monitor_start(callback: CallbackQuery) -> None:
    global _monitoring_task, _monitoring_stop_event
    if _is_monitoring_active():
        try:
            await callback.answer("⚠️ Мониторинг уже запущен", show_alert=False)
        except Exception:
            pass
        try:
            await callback.message.edit_text(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")
        except Exception:
            pass
        return
    _monitoring_stop_event = asyncio.Event()
    _monitoring_task = asyncio.create_task(continuous_monitoring_loop())
    try:
        await callback.answer("▶️ Мониторинг запущен!")
    except Exception:
        pass
    try:
        await callback.message.edit_text(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    logger.info("▶️ Мониторинг запущен пользователем через Telegram")


@dp.callback_query(F.data == "monitor_stop")
async def callback_monitor_stop(callback: CallbackQuery) -> None:
    global _monitoring_stop_event
    if not _is_monitoring_active():
        try:
            await callback.answer("ℹ️ Мониторинг уже остановлен", show_alert=False)
        except Exception:
            pass
        try:
            await callback.message.edit_text(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")
        except Exception:
            pass
        return
    if _monitoring_stop_event:
        _monitoring_stop_event.set()
    try:
        await callback.answer("⏹ Сигнал остановки отправлен")
    except Exception:
        pass
    try:
        await callback.message.edit_text(
            build_monitor_text() + "\n\n<i>⏳ Цикл завершит текущую итерацию и остановится...</i>",
            reply_markup=build_monitor_keyboard(), parse_mode="HTML"
        )
    except Exception:
        pass
    logger.info("⏹ Запрос остановки мониторинга от пользователя через Telegram")


@dp.callback_query(F.data == "monitor_schedule_toggle")
async def callback_monitor_schedule_toggle(callback: CallbackQuery) -> None:
    global _auto_schedule_enabled
    if _scheduler is None:
        try:
            await callback.answer("⚠️ Планировщик не инициализирован", show_alert=True)
        except Exception:
            pass
        return
    if _auto_schedule_enabled:
        try:
            _scheduler.remove_job(_SCHEDULE_JOB_ID)
            logger.info("⏰ Авто-расписание выключено пользователем")
        except Exception:
            pass
        _auto_schedule_enabled = False
        try:
            await callback.answer("🔴 Авто-расписание выключено")
        except Exception:
            pass
    else:
        try:
            from main import scheduled_job
            _scheduler.add_job(
                scheduled_job, 'interval', minutes=15, jitter=60,
                id=_SCHEDULE_JOB_ID, replace_existing=True
            )
            _auto_schedule_enabled = True
            logger.info("⏰ Авто-расписание включено пользователем (каждые 15 мин)")
            try:
                await callback.answer("🟢 Авто-расписание включено (каждые 15 мин)")
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Ошибка включения авто-расписания: {e}", exc_info=True)
            try:
                await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
            except Exception:
                pass
            return
    try:
        await callback.message.edit_text(build_monitor_text(), reply_markup=build_monitor_keyboard(), parse_mode="HTML")
    except Exception:
        pass

@dp.message(Command("synthetic"))
async def command_synthetic_handler(message: types.Message) -> None:
    """Запуск сканирования синтетических коридоров по запросу."""
    await message.answer("🔄 Запускаю математический поиск синтетических коридоров (Polymarket). Это займет пару минут...")
    try:
        from services.synthetic_corridor_scanner import run_synthetic_corridor_scan
        from services.notifications import send_synthetic_corridor_alerts
        
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
    from datetime import datetime, timezone
    import os
    from agents.shared.python.db import get_memory, get_connection
    
    # Сбор данных LLMHealthGate
    state = llm_health_gate.state
    state_emoji = "🟢" if state == "HEALTHY" else "🟡" if state == "DEGRADED" else "🔴"
    
    now = datetime.now(timezone.utc)
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
    
    # Ротация ключей
    primary_key = os.getenv("GOOGLE_API_KEY", "")
    secondary_key = os.getenv("GOOGLE_API_KEY_SECONDARY", "")
    third_key = os.getenv("GOOGLE_API_KEY_THIRD", "")
    active_keys = [k for k in [primary_key, secondary_key, third_key] if k and k.strip()]
    num_keys = len(active_keys)
    key_rr_idx = int(get_memory("gem_key_rr_index") or 0)
    current_key_num = (key_rr_idx % num_keys) + 1 if num_keys > 0 else 0
    
    lines.append("")
    lines.append("🔑 <b>Ротация ключей Gemini:</b>")
    lines.append(f"● Активно ключей: {num_keys}")
    lines.append(f"● Текущий ключ: #{current_key_num} (индекс: {key_rr_idx})")
    
    # Аналитика затрат
    token_stats_24h = {"in": 0, "out": 0, "total": 0, "cost": 0.0}
    token_stats_7d = {"in": 0, "out": 0, "total": 0, "cost": 0.0}
    top_agent_24h = "Нет данных"
    top_model_24h = "Нет данных"
    top_agent_7d = "Нет данных"
    top_model_7d = "Нет данных"
    
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            
            # Агрегация по моделям за 24 часа
            cursor.execute("""
                SELECT model_name, SUM(input_tokens) as in_t, SUM(output_tokens) as out_t, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-1 day')
                GROUP BY model_name
            """)
            for r in cursor.fetchall():
                in_t = r['in_t'] or 0
                out_t = r['out_t'] or 0
                tot_t = r['tot_t'] or 0
                token_stats_24h["in"] += in_t
                token_stats_24h["out"] += out_t
                token_stats_24h["total"] += tot_t
                token_stats_24h["cost"] += estimate_llm_cost(r['model_name'] or "", in_t, out_t)

            # Агрегация по моделям за 7 дней
            cursor.execute("""
                SELECT model_name, SUM(input_tokens) as in_t, SUM(output_tokens) as out_t, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY model_name
            """)
            for r in cursor.fetchall():
                in_t = r['in_t'] or 0
                out_t = r['out_t'] or 0
                tot_t = r['tot_t'] or 0
                token_stats_7d["in"] += in_t
                token_stats_7d["out"] += out_t
                token_stats_7d["total"] += tot_t
                token_stats_7d["cost"] += estimate_llm_cost(r['model_name'] or "", in_t, out_t)

            # Топ-агент за 24 часа
            cursor.execute("""
                SELECT agent_name, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-1 day')
                GROUP BY agent_name
                ORDER BY tot_t DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                top_agent_24h = f"{row['agent_name']} ({row['tot_t']:,} токенов)"

            # Топ-модель за 24 часа
            cursor.execute("""
                SELECT model_name, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-1 day')
                GROUP BY model_name
                ORDER BY tot_t DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                top_model_24h = f"{row['model_name']} ({row['tot_t']:,} токенов)"

            # Топ-агент за 7 дней
            cursor.execute("""
                SELECT agent_name, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY agent_name
                ORDER BY tot_t DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                top_agent_7d = f"{row['agent_name']} ({row['tot_t']:,} токенов)"

            # Топ-модель за 7 дней
            cursor.execute("""
                SELECT model_name, SUM(total_tokens) as tot_t
                FROM llm_calls
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY model_name
                ORDER BY tot_t DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                top_model_7d = f"{row['model_name']} ({row['tot_t']:,} токенов)"

    except Exception as db_err:
        logger.error(f"Ошибка получения статистики токенов из БД: {db_err}", exc_info=True)
        
    lines.append("")
    lines.append("📊 <b>Аналитика затрат API (24 часа):</b>")
    lines.append(f"● Всего токенов: {token_stats_24h['total']:,} (Вход: {token_stats_24h['in']:,} | Выход: {token_stats_24h['out']:,})")
    lines.append(f"● Стоимость: ${token_stats_24h['cost']:.4f} USD")
    lines.append(f"● Топ-агент: {top_agent_24h}")
    lines.append(f"● Топ-модель: {top_model_24h}")

    lines.append("")
    lines.append("📆 <b>Аналитика затрат API (7 дней):</b>")
    lines.append(f"● Всего токенов: {token_stats_7d['total']:,} (Вход: {token_stats_7d['in']:,} | Выход: {token_stats_7d['out']:,})")
    lines.append(f"● Стоимость: ${token_stats_7d['cost']:.4f} USD")
    lines.append(f"● Топ-агент: {top_agent_7d}")
    lines.append(f"● Топ-модель: {top_model_7d}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("status"))
async def command_status_handler(message: types.Message) -> None:
    from agents.shared.python.db import DB_PATH, get_connection, get_memory_stats, get_memory
    from config import SCAN_LIMIT_DEFAULT
    
    # Получаем настройки и метрики из БД
    last_scan_str = "Неизвестно"
    scan_limit = SCAN_LIMIT_DEFAULT
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
    is_monitoring = _is_monitoring_active()
    monitoring_status = "🟢 Активен" if is_monitoring else "🔴 Остановлен"
    schedule_status = "🟢 Вкл (15 мин)" if _auto_schedule_enabled else "🔴 Выкл"

    status_text = (
        "📊 <b>Статус системы:</b>\n\n"
        f"● <b>Мониторинг:</b> {monitoring_status}\n"
        f"● <b>Авто-расписание:</b> {schedule_status}\n"
        "● <b>Оркестратор NEXUS:</b> 🟢 Активен\n"
        "● <b>Агенты (SCOUT, SWING, SHADOW, ARBITRAGE):</b> 🟢 Готовы\n"
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
        f"🕒 <b>Последнее сканирование:</b>\n<code>{last_scan_str}</code>\n"
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
    db_stats = await asyncio.to_thread(get_db_stats)
    try:
        from core.math_filter_metrics import get_stats as get_math_stats
        math_stats = await asyncio.to_thread(get_math_stats)
        if math_stats and "rows" in math_stats and math_stats["rows"]:
            db_stats += "\n\n📐 <b>Math Gate (7д):</b>"
            for row in math_stats["rows"]:
                db_stats += f"\n- {row['decision']} ({row['arbitrage_type']}): <b>{row['cnt']}</b> (avg spread: {row['avg_spread']:.1f}%, confirmed: {row['confirmed']})"
    except Exception as e:
        logger.warning(f"Error adding math stats to telegram /stats command: {e}")
    await message.answer(db_stats)

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

@dp.message(Command("gate_stats"))
async def command_gate_stats_handler(message: types.Message) -> None:
    from agents.shared.python.db import get_connection
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT 
                    SUM(total) as total_scanned,
                    SUM(passed) as total_passed,
                    SUM(blocked_no_volume) as total_blocked_vol,
                    SUM(blocked_no_whales) as total_blocked_whales
                FROM gate_metrics
                WHERE created_at >= datetime('now', '-24 hours')
            """).fetchone()
        
        if not row or row["total_scanned"] is None or row["total_scanned"] == 0:
            text = "🛡️ <b>On-chain Gatekeeper (24ч):</b>\n\n<i>Нет данных за последние 24 часа.</i>"
        else:
            total = row["total_scanned"]
            passed = row["total_passed"]
            blocked_vol = row["total_blocked_vol"]
            blocked_whales = row["total_blocked_whales"]
            blocked = total - passed
            
            passed_pct = (passed / total * 100) if total > 0 else 0
            blocked_pct = (blocked / total * 100) if total > 0 else 0
            
            saved_usd = blocked * 0.0015
            
            text = (
                f"🛡️ <b>Статистика On-chain Gatekeeper (24ч):</b>\n\n"
                f"📊 Всего проверено рынков: <b>{total}</b>\n"
                f"✅ Пропущено к анализу LLM: <b>{passed}</b> ({passed_pct:.1f}%)\n"
                f"⛔ Заблокировано гейтом: <b>{blocked}</b> ({blocked_pct:.1f}%)\n"
                f"  ↳ Из-за низкого объема: <b>{blocked_vol}</b>\n"
                f"  ↳ Из-за отсутствия китов: <b>{blocked_whales}</b>\n\n"
                f"💰 <b>Экономия бюджета:</b>\n"
                f"• Сэкономлено вызовов LLM: <b>{blocked}</b>\n"
                f"• Оценочная экономия: <b>${saved_usd:.2f}</b> (при $0.0015 за вызов)"
            )
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Ошибка получения статистики гейта: {e}")


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
            logger.error(f"[TrendHunter] Необработанное исключение: {e}", exc_info=True)
            from agents.shared.python.db import get_memory
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if chat_id:
                try:
                    await bot.send_message(chat_id=chat_id, text=f"⚠️ TrendHunter упал: {e}")
                except Exception as tg_err:
                    logger.error(f"[TrendHunter] Не удалось отправить алерт об ошибке в Telegram: {tg_err}")

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
    
    if "gemini_round_robin" in model_lower:
        return "🔄 Gemini Auto RR"
    elif "gemini-2.5-flash" in model_lower:
        return "✨ Gemini 2.5 Flash"
    elif "gemini-2.0-flash-lite" in model_lower:
        return "⚡ Gemini 2.0 Flash Lite"
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
    mapping[_shorten_key("gemini_round_robin")] = ("gemini", "gemini_round_robin", "🔄 Gemini Auto RR")
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

    is_default = False
    if current_model_id == "Дефолт (.env)":
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

    buttons = [
        [InlineKeyboardButton(text=val[2], callback_data=f"sm_{agent}_{key}")]
        for key, val in models_mapping.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_models")])

    from aiogram.exceptions import TelegramBadRequest
    try:
        await callback.message.edit_text(
            f"🤖 <b>Настройка модели для: {agent}</b>\n\n"
            f"Текущая модель: <code>{nice_model_name}</code>\n\n"
            f"Выберите новую модель:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Error in callback_set_agent_model: {e}")

    await callback.answer()

@dp.callback_query(F.data.startswith("sm_"))
async def callback_save_model(callback: CallbackQuery) -> None:
    models_mapping = get_dynamic_models_mapping()
    
    # Отрезаем префикс "sm_"
    data = callback.data[3:]
    
    model_key = None
    agent = ""
    
    # Пробуем разобрать по известным префиксам агентов
    for possible_agent in ["NEXUS", "SCOUT", "SWING", "SHADOW", "ARBITRAGE"]:
        if data.startswith(f"{possible_agent}_"):
            agent = possible_agent
            model_key = data[len(possible_agent) + 1:]
            break
            
    if not model_key:
        # Ищем точный ключ в маппинге с конца
        for key in models_mapping:
            if data.endswith(f"_{key}"):
                model_key = key
                agent = data[:-len(key) - 1]
                break
            
    # Fallback на случай, если ключ не нашелся в маппинге
    if not model_key:
        parts = data.rsplit("_", 1)
        if len(parts) == 2:
            agent, model_key = parts
        else:
            agent = data
            model_key = ""
        
    provider, model_name, _ = models_mapping.get(model_key, ("openrouter", "meta-llama/llama-3.3-70b-instruct:free", "🦙 Llama 3.3"))
    
    from agents.shared.python.db import save_memory
    config = {"provider": provider, "model": model_name}
    await asyncio.to_thread(save_memory, f"agent_config_{agent}", config)
    
    engine = get_core_engine()
    if engine:
        if agent == "SCOUT" and hasattr(engine, 'scout'): engine.scout.model = model_name
        if agent == "SWING" and hasattr(engine, 'swing'): engine.swing.model = model_name
        if agent == "SHADOW" and hasattr(engine, 'shadow'): engine.shadow.model = model_name
        if agent == "NEXUS" and hasattr(engine, 'nexus'): engine.nexus.model_name = model_name
    
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
        # Читаем только конец файла с помощью seek
        with open(LOG_PATH, "rb") as f:
            try:
                f.seek(-10000, 2)  # прыгаем на 10 КБ от конца
            except OSError:
                f.seek(0)
            content_bytes = f.read()
            content = content_bytes.decode("utf-8", errors="replace")
            last_lines = content.splitlines()[-10:]
            logs = "\n".join(last_lines)
        # Экранируем спецсимволы для HTML
        safe_logs = logs.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        await message.answer(f"📜 <b>Последние логи:</b>\n<pre>{safe_logs}</pre>")
    except Exception as e:
        await message.answer(f"Ошибка чтения логов: {e}")

@dp.message(Command("restart"))
async def command_restart_handler(message: types.Message) -> None:
    """Останавливает процесс бота. Менеджер процессов (systemd/PM2) автоматически его перезапустит."""
    await message.answer("🔄 <b>Перезапуск через 5 секунд...</b>", parse_mode="HTML")
    logging.warning("Получена команда /restart. Закрываю сессию и завершаю процесс...")
    
    await asyncio.sleep(5)  # было 3, дать scheduler паузу

    # Изящное завершение сессии Telegram
    try:
        await bot.session.close()
    except Exception:
        pass

    import signal
    # Отправляем сигнал SIGTERM собственному процессу для запуска graceful shutdown в main.py
    os.kill(os.getpid(), signal.SIGTERM)

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
        logger.error(f"[ARBITRAGE] Ошибка: {error_text}")
        await status_msg.edit_text(f"❌ <b>Ошибка арбитражного сканирования:</b>\n<pre>{str(e)[:400]}</pre>", parse_mode="HTML")

@dp.message(Command("corridor"))
async def command_corridor_handler(message: types.Message) -> None:
    status_msg = await message.answer("🕐 <b>Сканирую временные коридоры...</b>", parse_mode="HTML")
    try:
        from services.temporal_corridor_scanner import run_temporal_corridor_scan
        signals = await asyncio.to_thread(
            run_temporal_corridor_scan, poly_limit=500, budget=200.0
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

    import html
    category = html.escape(state.get("category", "Авто-микс"))
    stage = html.escape(state.get("stage", "В процессе"))
    cur_idx = state.get('current_market_index', 0)
    tot = state.get('total_markets', 0)
    title = html.escape(state.get('current_market_title', 'Поиск...'))
    url = state.get('current_market_url', '')
    scout = html.escape(state.get('scout_status', '⏳ Ожидает'))
    swing = html.escape(state.get('swing_status', '⏳ Ожидает'))
    shadow = html.escape(state.get('shadow_status', '⏳ Ожидает'))
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


from agents.shared.scan_categories import SCAN_CATEGORIES

def build_scan_keyboard() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(
        text=v["label"], callback_data=f"scan_{k}"
    )] for k, v in SCAN_CATEGORIES.items()]
    buttons.insert(0, [InlineKeyboardButton(
        text="🌐 Все (авто-микс)", callback_data="scan_all"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("scan"))
async def command_scan_handler(message: types.Message) -> None:
    engine = get_core_engine()
    is_busy = _scan_lock.locked() or engine._scan_lock.locked()

    if is_busy:
        status_text = get_active_scan_status_text()
        header = (
            f"{status_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⬇️ <b>Выберите категорию — запустится после окончания:</b>"
        )
    else:
        header = "🔍 <b>Выберите категорию для сканирования:</b>"

    keyboard = build_scan_keyboard()
    await message.answer(
        header,
        reply_markup=keyboard,
        parse_mode="HTML",
        link_preview_options=LinkPreviewOptions(is_disabled=True)
    )

@dp.callback_query(F.data.startswith("scan_"))
async def callback_scan_handler(callback: CallbackQuery) -> None:
    # Дедупликация: игнорируем повторно доставленные callback'и
    async with _callback_dedup_lock:
        is_processed = callback.id in _processed_callback_ids
        if not is_processed:
            _processed_callback_ids.append(callback.id)
            
    if is_processed:
        await callback.answer()
        return

    engine = get_core_engine()
    category = callback.data.replace("scan_", "")
    is_penny = (category == "penny_stocks")
    
    active_lock = _penny_scan_lock if is_penny else _scan_lock
    active_engine_lock = engine._penny_scan_lock if is_penny else engine._scan_lock

    if _favourite_compound_lock.locked():
        await callback.answer()
        await callback.message.answer(
            "⚠️ Сейчас выполняется Favourite Compounding скан. Подождите.",
            parse_mode="HTML"
        )
        return

    if active_lock.locked() or active_engine_lock.locked():
        await callback.answer()
        scan_type_str = "Penny Stocks " if is_penny else ""
        await callback.message.answer(
            f"⚠️ Сканирование {scan_type_str}уже выполняется. Пожалуйста, подождите завершения текущего цикла.",
            parse_mode="HTML"
        )
        return

    if category == "all":
        category_param = None
        cat_name = "Все рынки (авто-микс)"
    else:
        category_param = category
        from agents.shared.scan_categories import SCAN_CATEGORIES
        if category in SCAN_CATEGORIES:
            cat_name = SCAN_CATEGORIES[category]["label"]
        else:
            cat_name = category

    async with active_lock:
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
            while active_lock.locked() or summaries_queue:
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
                if current_state and active_lock.locked():
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
        # Ждем завершения фоновой задачи обновления (она сама завершится, когда очередь summaries_queue опустеет и _scan_lock освободится)
        try:
            await asyncio.wait_for(updater_task, timeout=10.0)
        except asyncio.TimeoutError:
            updater_task.cancel()
            try:
                await updater_task
            except asyncio.CancelledError:
                pass


def _get_emoji_number(num: int) -> str:
    digits = {
        '0': '0️⃣', '1': '1️⃣', '2': '2️⃣', '3': '3️⃣', '4': '4️⃣',
        '5': '5️⃣', '6': '6️⃣', '7': '7️⃣', '8': '8️⃣', '9': '9️⃣'
    }
    return "".join(digits[d] for d in str(num))


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
    
    for idx, s in enumerate(chunk, 1):
        global_idx = start_idx + idx
        edge_pct = (s['edge'] or 0) * 100
        target = s.get('target_outcome', 'YES')
        price = s['market_price']
        if target.upper() == 'NO':
            price = 1.0 - price
            
        title_safe = s['title'].replace('<', '&lt;').replace('>', '&gt;')
        summary_safe = s['summary'].replace('<', '&lt;').replace('>', '&gt;')
        if len(summary_safe) > 500:
            summary_safe = summary_safe[:500] + "..."
            
        emoji = _get_emoji_number(global_idx)
        response += (
            f"{emoji} <b>{title_safe}</b>\n"
            f"🎯 <b>Рекомендация: Покупать {target}</b> (по цене ~{price:.3f})\n"
            f"📈 Edge (преимущество): <b>+{edge_pct:.1f}%</b> | Уверенность: {s['confidence']}\n"
            f"📝 {summary_safe}\n"
            f"🔗 <a href='{s['url']}'>Открыть рынок</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "ideas_page")
    
    # Добавляем кнопки для удаления (архивирования) каждого сигнала на текущей странице
    delete_buttons = []
    for idx, s in enumerate(chunk, 1):
        truncated_id = s['id'][:30]
        global_idx = start_idx + idx
        # Делаем короткий заголовок, чтобы кнопка помещалась и выглядела эстетично
        short_title = s['title'][:20] + "..." if len(s['title']) > 20 else s['title']
        delete_buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ Удалить {global_idx}: {short_title}",
                callback_data=f"del_sig_{page}_{truncated_id}"
            )
        ])
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=delete_buttons + keyboard.inline_keyboard)
    
    await send_or_edit(message_or_callback, response, keyboard)

async def send_penny_page(message_or_callback, page: int = 0) -> None:
    signals = await asyncio.to_thread(get_signals, 100)
    penny_signals = [s for s in signals if s.get('market_price') is not None and min(s['market_price'], 1.0 - s['market_price']) <= 0.05]
    
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


@dp.message(Command("penny"))
async def command_penny_handler(message: types.Message) -> None:
    await send_penny_menu(message)

async def send_penny_menu(message_or_callback) -> None:
    from agents.shared.python.db import get_penny_stocks_stats, get_active_penny_stocks
    stats = await asyncio.to_thread(get_penny_stocks_stats)
    active = await asyncio.to_thread(get_active_penny_stocks)
    
    discovery_status = "🟢 Активен (4ч)"
    monitor_status = "🟢 Активен (15м)"
    
    text = (
        f"🪙 <b>Меню Penny Stocks (дешевые рынки)</b>\n\n"
        f"📋 <b>Статус мониторинга:</b>\n"
        f"  • Поиск новых: {discovery_status}\n"
        f"  • Обновление цен/исходов: {monitor_status}\n\n"
        f"📊 <b>Статистика прогнозов:</b>\n"
        f"  • Активных на отслеживании: <b>{len(active)}</b>\n"
        f"  • Завершено рынков: <b>{stats['resolved']}</b>\n"
        f"  • Точность (Win Rate): <b>{stats['win_rate']:.1%}</b> (совпало: {stats['correct']})\n"
        f"  • Средний Edge: <b>+{stats['avg_edge']*100:.1f}%</b>\n"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪙 Активные рынки", callback_data="penny_active_0")],
        [InlineKeyboardButton(text="🗄 История исходов", callback_data="penny_history_0")],
        [
            InlineKeyboardButton(text="🚀 Искать новые", callback_data="scan_penny_stocks"),
            InlineKeyboardButton(text="🔄 Меню", callback_data="penny_menu")
        ]
    ])
    
    await send_or_edit(message_or_callback, text, keyboard)

@dp.callback_query(F.data == "penny_menu")
async def callback_penny_menu(callback: CallbackQuery) -> None:
    await send_penny_menu(callback)
    await callback.answer()

@dp.callback_query(F.data.startswith("penny_active_"))
async def callback_penny_active(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_penny_active_page(callback, page)
    await callback.answer()

@dp.callback_query(F.data.startswith("penny_history_"))
async def callback_penny_history(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_penny_history_page(callback, page)
    await callback.answer()

async def send_penny_active_page(message_or_callback, page: int = 0) -> None:
    from agents.shared.python.db import get_active_penny_stocks
    active = await asyncio.to_thread(get_active_penny_stocks)
    
    if not active:
        text = "🪙 Нет активных дешевых рынков на отслеживании. Запустите сканирование."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Искать новые", callback_data="scan_penny_stocks")],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="penny_menu")]
        ])
        await send_or_edit(message_or_callback, text, keyboard)
        return

    chunk_size = 5
    total_pages = (len(active) + chunk_size - 1) // chunk_size
    if page >= total_pages:
        page = 0
        
    start_idx = page * chunk_size
    chunk = active[start_idx:start_idx + chunk_size]
    
    response = f"🪙 <b>Активные Penny Stocks ({start_idx + 1}-{min(start_idx + chunk_size, len(active))} из {len(active)}):</b>\n\n"
    
    for stock in chunk:
        init_price_cents = int(round(stock['initial_price'] * 100))
        curr_price_cents = int(round(stock['current_price'] * 100))
        max_price_cents = int(round(stock['max_price_seen'] * 100))
        
        pred = stock['predicted_outcome'] or 'Нет прогноза'
        edge_str = f"+{stock['edge']*100:.1f}%" if stock['edge'] is not None else "N/A"
        
        title_safe = stock['title'].replace('<', '&lt;').replace('>', '&gt;')
        response += (
            f"📍 <b>{title_safe}</b>\n"
            f"🎯 Прогноз: <b>{pred}</b> (Edge: {edge_str})\n"
            f"📈 Начальная: {init_price_cents}¢ | Текущая: <b>{curr_price_cents}¢</b> (пик: {max_price_cents}¢)\n"
            f"🔗 <a href='{stock['url']}'>Открыть рынок</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "penny_active")
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="penny_menu")])
    await send_or_edit(message_or_callback, response, keyboard)

async def send_penny_history_page(message_or_callback, page: int = 0) -> None:
    from agents.shared.python.db import get_penny_stocks_history
    history = await asyncio.to_thread(get_penny_stocks_history, 100)
    
    if not history:
        text = "🗄 История пуста. Закрытые рынки появятся здесь позже."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="penny_menu")]
        ])
        await send_or_edit(message_or_callback, text, keyboard)
        return

    chunk_size = 5
    total_pages = (len(history) + chunk_size - 1) // chunk_size
    if page >= total_pages:
        page = 0
        
    start_idx = page * chunk_size
    chunk = history[start_idx:start_idx + chunk_size]
    
    response = f"🗄 <b>История исходов Penny Stocks ({start_idx + 1}-{min(start_idx + chunk_size, len(history))} из {len(history)}):</b>\n\n"
    
    for stock in chunk:
        init_price_cents = int(round(stock['initial_price'] * 100))
        final_price_cents = int(round(stock['current_price'] * 100))
        max_price_cents = int(round(stock['max_price_seen'] * 100))
        
        pred = stock['predicted_outcome']
        act = stock['actual_outcome']
        is_correct = pred and act and pred.upper() == act.upper()
        result_emoji = "✅" if is_correct else "❌"
        
        title_safe = stock['title'].replace('<', '&lt;').replace('>', '&gt;')
        response += (
            f"{result_emoji} <b>{title_safe}</b>\n"
            f"🎯 Прогноз: <b>{pred}</b> | Исход: <b>{act}</b>\n"
            f"📉 Старт: {init_price_cents}¢ | Финиш: {final_price_cents}¢ (пик: {max_price_cents}¢)\n"
            f"🔗 <a href='{stock['url']}'>Открыть рынок</a>\n\n"
        )
        
    keyboard = build_paginated_keyboard(page, total_pages, "penny_history")
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="penny_menu")])
    await send_or_edit(message_or_callback, response, keyboard)


@dp.message(Command("ideas"))
async def command_ideas_handler(message: types.Message) -> None:
    await send_ideas_page(message, page=0)

@dp.callback_query(F.data.startswith("ideas_page_"))
async def callback_ideas_page_handler(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_ideas_page(callback, page=page)

@dp.callback_query(F.data.startswith("del_sig_"))
async def callback_delete_signal(callback: CallbackQuery) -> None:
    """Удаляет (архивирует) сигнал из списка идей."""
    parts = callback.data.split("_")
    # callback.data format: del_sig_{page}_{truncated_id}
    # parts looks like: ['del', 'sig', '{page}', '{truncated_id_part1}', ...]
    if len(parts) < 4:
        await callback.answer("⚠️ Неверный формат ID.", show_alert=True)
        return
        
    try:
        page = int(parts[2])
    except ValueError:
        page = 0
        
    truncated_id = "_".join(parts[3:])
    
    # Архивируем в фоновом пуле
    success = await asyncio.to_thread(archive_signal_by_id, truncated_id)
    
    if success:
        await callback.answer("🗑️ Идея архивирована и убрана из списка.", show_alert=True)
    else:
        await callback.answer("⚠️ Идея не найдена или уже удалена.", show_alert=True)
        
    # Перерисовываем страницу
    await send_ideas_page(callback, page=page)

@dp.message(Command("history"))
async def command_history_handler(message: types.Message) -> None:
    await send_history_page(message, page=0)

@dp.callback_query(F.data.startswith("history_page_"))
async def callback_history_page_handler(callback: CallbackQuery) -> None:
    page = int(callback.data.split("_")[2])
    await send_history_page(callback, page=page)



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
            InlineKeyboardButton(text="📥 В идеи", callback_data=f"add_idea_{market_id[:40]}"),
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


def _save_manual_signal_sync(full_market_id: str, market_price: float) -> bool:
    """Синхронное сохранение ручного сигнала, защищённое threading.Lock."""
    from agents.shared.python.db import get_connection, save_signal
    from core.models import Signal
    from datetime import datetime, timezone
    import time
    
    with _manual_signal_lock:
        scout_edge = 0.15
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT scout_edge FROM idea_audit WHERE market_id = ? ORDER BY created_at DESC LIMIT 1",
                (full_market_id,)
            )
            row = cursor.fetchone()
            if row and row["scout_edge"] is not None:
                try:
                    scout_edge = float(row["scout_edge"])
                except (KeyError, TypeError, IndexError):
                    try:
                        scout_edge = float(row[0])
                    except Exception:
                        pass
                        
        signal = Signal(
            id=f"manual_{full_market_id[:20]}_{int(time.time())}",
            type="MANUAL",
            market_id=full_market_id,
            platform="polymarket",
            target_outcome="YES",
            trade_action="buy",
            entry_price=market_price,
            position_size_usd=0.0,
            edge=scout_edge,
            confidence=0.8,
            priority="medium",
            summary="Ручное добавление пользователем",
            details="Пользователь вручную добавил этот рынок в список торговых идей.",
            status="PENDING",
            created_at=datetime.now(timezone.utc)
        )
        return save_signal(signal, None, True)


@dp.callback_query(F.data.startswith("add_idea_"))
async def callback_add_idea(callback: CallbackQuery) -> None:
    """'В идеи' — вручную добавляет рынок в список торговых идей (/ideas)."""
    market_id = callback.data[len("add_idea_"):]
    
    from agents.shared.python.db import get_connection, save_signal
    from core.models import Signal
    import time
    
    db_market = None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, price, url FROM markets WHERE id = ? OR id LIKE ?",
            (market_id, f"{market_id}%")
        )
        row = cursor.fetchone()
        if row:
            db_market = dict(row)
            
    if not db_market:
        await callback.answer("⚠️ Ошибка: Рынок не найден в базе данных. Не удалось добавить.", show_alert=True)
        return
        
    full_market_id = db_market["id"]
    market_title = db_market["title"]
    market_price = db_market["price"]
    
    inserted = await asyncio.to_thread(_save_manual_signal_sync, full_market_id, market_price)
    if not inserted:
        await callback.answer("ℹ️ Этот рынок уже находится в списке торговых идей /ideas!", show_alert=True)
        return
    
    # Обновляем клавиатуру, показываем что добавлено
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Игнорировать", callback_data=f"ignore_mkt_{full_market_id[:40]}"),
            InlineKeyboardButton(text="👁 Следить", callback_data=f"watch_mkt_{full_market_id[:40]}"),
        ],
        [
            InlineKeyboardButton(text="✅ Добавлено в /ideas", callback_data="noop")
        ]
    ])
    
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception:
        pass
        
    await callback.answer("✅ Рынок успешно добавлен в список торговых идей /ideas!", show_alert=True)


@dp.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery) -> None:
    await callback.answer()


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
    """Извлекает заголовок рынка из текста сообщения (сначала ищет ссылку, потом фолбек на текст)."""
    import re
    try:
        text = message.text or message.caption or ""
        
        # Ищем первую HTML ссылку <a>...</a>
        match = re.search(r"<a href=['\"][^'\"]*['\"][^>]*>(.*?)</a>", text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r"<[^>]+>", "", title)  # Убираем внутренние теги
            if len(title) > 3:
                return title[:80]
                
        # Фолбек: ищем первую содержательную строку
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Пропускаем строки с эмодзи-заголовками
            if any(x in line for x in ("🗣️", "🎯", "👁", "🚨", "🔔")):
                continue
            line_clean = re.sub(r"<[^>]+>", "", line).strip()
            if len(line_clean) > 5:
                return line_clean[:80]
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


# ── Управление калибровкой и оценкой (Evaluation Engine) ──────────────────────

@dp.message(Command("eval"))
async def command_eval_handler(message: types.Message) -> None:
    """Запускает полный цикл оценки по всем стратегиям вручную."""
    await message.answer("🔄 Запуск оценки торговых стратегий за последние 30 дней. Сбор результатов Polymarket и расчет метрик...")
    try:
        from core.eval.evaluation_engine import EvaluationEngine
        engine = EvaluationEngine()
        await engine.run_full_evaluation()
        await message.answer("✅ Оценка успешно завершена! Сводный отчет отправлен в чат.")
    except Exception as e:
        logger.error(f"Ошибка при ручном запуске оценки: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при запуске оценки: {e}")

@dp.message(Command("eval_status"))
async def command_eval_status_handler(message: types.Message) -> None:
    """Показывает текущие торговые пороги систем."""
    try:
        from core.eval.calibration_store import CalibrationStore
        from core.eval.signal_logger import StrategyType
        import config
        
        store = CalibrationStore()
        
        # scout
        scout_val = await store.get_latest_applied_value("min_edge", StrategyType.SCOUT.value)
        if scout_val is None:
            scout_val = getattr(config, "MIN_EDGE_DEFAULT", 0.05)
            
        # synthetic corridor
        synthetic_val = await store.get_latest_applied_value("min_spread", StrategyType.SYNTHETIC_CORRIDOR.value)
        if synthetic_val is None:
            synthetic_val = 0.008  # 0.8%
            
        # temporal corridor
        temporal_val = await store.get_latest_applied_value("min_spread", StrategyType.TEMPORAL_CORRIDOR.value)
        if temporal_val is None:
            temporal_val = 0.020  # 2.0%
            
        # cross platform
        cross_val = await store.get_latest_applied_value("min_spread", StrategyType.CROSS_PLATFORM.value)
        if cross_val is None:
            cross_val = 0.050  # 5.0%
            
        # whale
        whale_val = await store.get_latest_applied_value("whale_win_rate_threshold", StrategyType.WHALE.value)
        if whale_val is None:
            whale_val = getattr(config, "WHALE_GATE_MIN_CONFIDENCE", 0.70)
            
        status_text = (
            "⚙️ <b>Текущие торговые пороги систем:</b>\n\n"
            f"🕵️ <b>SCOUT:</b> min_edge = <code>{scout_val:.1%}</code>\n"
            f"🔬 <b>SYNTHETIC CORRIDOR:</b> min_spread = <code>{synthetic_val:.1%}</code>\n"
            f"⏳ <b>TEMPORAL CORRIDOR:</b> min_spread = <code>{temporal_val:.1%}</code>\n"
            f"🔄 <b>CROSS PLATFORM:</b> min_spread = <code>{cross_val:.1%}</code>\n"
            f"🐋 <b>WHALE FOLLOWING:</b> win_rate_threshold = <code>{whale_val:.0%}</code>\n"
        )
        await message.answer(status_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при получении статуса порогов: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при получении статуса: {e}")

@dp.message(Command("eval_history"))
async def command_eval_history_handler(message: types.Message) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🕵️ Scout",            callback_data="evalhist_scout"),
            InlineKeyboardButton(text="📊 Synthetic Corridor", callback_data="evalhist_synthetic_corridor"),
        ],
        [
            InlineKeyboardButton(text="⏱ Temporal Corridor",  callback_data="evalhist_temporal_corridor"),
            InlineKeyboardButton(text="🌐 Cross Platform",     callback_data="evalhist_cross_platform"),
        ],
        [
            InlineKeyboardButton(text="🐳 Whale",              callback_data="evalhist_whale"),
        ],
    ])
    await message.answer(
        "📚 <b>История калибровок</b>\nВыберите стратегию:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(lambda c: c.data and c.data.startswith("evalhist_"))
async def callback_eval_history_handler(callback: types.CallbackQuery) -> None:
    await callback.answer()
    strategy_input = callback.data.replace("evalhist_", "")
    
    from core.eval.signal_logger import StrategyType
    strategy_map = {
        "scout":               StrategyType.SCOUT,
        "synthetic_corridor":  StrategyType.SYNTHETIC_CORRIDOR,
        "temporal_corridor":   StrategyType.TEMPORAL_CORRIDOR,
        "cross_platform":      StrategyType.CROSS_PLATFORM,
        "whale":               StrategyType.WHALE,
    }
    strategy = strategy_map.get(strategy_input)
    if not strategy:
        await callback.message.answer("❌ Неизвестная стратегия")
        return
        
    try:
        from core.eval.calibration_store import CalibrationStore
        store = CalibrationStore()
        history = await store.get_strategy_history(strategy.value, 10)
        
        if not history:
            await callback.message.answer(f"📜 История калибровок для стратегии <b>{strategy.value}</b> пуста.", parse_mode="HTML")
            return
            
        text = f"📜 <b>История калибровок ({strategy.value.upper()}):</b>\n\n"
        for idx, record in enumerate(history, 1):
            is_applied = "✅ Применено" if record.auto_applied else "⏳ Ожидает ручного подтверждения"
            is_pct = record.param_name in ("min_edge", "min_spread")
            if is_pct:
                prev = f"{record.previous_value:.1%}"
                curr = f"{record.param_value:.1%}"
            else:
                prev = f"{record.previous_value:.2f}"
                curr = f"{record.param_value:.2f}"
                
            text += (
                f"<b>{idx}. Предложение #{record.id}</b>\n"
                f"• Параметр: <code>{record.param_name}</code>\n"
                f"• Изменение: {prev} → <b>{curr}</b>\n"
                f"• Статус: {is_applied}\n"
                f"• Обоснование: <i>{record.reason}</i>\n"
                f"• Дата: {record.updated_at}\n\n"
            )
            
        await callback.message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при получении истории калибровок: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка при получении истории: {e}")

@dp.message(Command("eval_apply"))
async def command_eval_apply_handler(message: types.Message) -> None:
    """Применяет калибровочное предложение вручную."""
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer(
                "⚠️ Укажите ID калибровочного предложения для применения.\n"
                "Использование: <code>/eval_apply &lt;id&gt;</code>",
                parse_mode="HTML"
            )
            return
            
        try:
            suggestion_id = int(args[1])
        except ValueError:
            await message.answer("❌ ID предложения должен быть целым числом.")
            return
            
        from core.eval.calibration_store import CalibrationStore
        store = CalibrationStore()
        success = await store.apply_suggestion(suggestion_id)
        
        if success:
            await message.answer(f"✅ Предложение калибровки #{suggestion_id} успешно применено. Новые параметры загружены в систему.")
        else:
            await message.answer(f"❌ Не удалось применить предложение #{suggestion_id}. Возможно, ID не существует или уже применен.")
    except Exception as e:
        logger.error(f"Ошибка при применении предложения: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при применении предложения: {e}")

@dp.message(Command("eval_rollback"))
async def command_eval_rollback_handler(message: types.Message) -> None:
    """Откатывает калибровочное предложение вручную."""
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer(
                "⚠️ Укажите ID примененной записи для отката.\n"
                "Использование: <code>/eval_rollback &lt;id&gt;</code>",
                parse_mode="HTML"
            )
            return
            
        try:
            suggestion_id = int(args[1])
        except ValueError:
            await message.answer("❌ ID записи должен быть целым числом.")
            return
            
        from core.eval.calibration_store import CalibrationStore
        store = CalibrationStore()
        success = await store.rollback(suggestion_id)
        
        if success:
            await message.answer(f"✅ Успешный откат изменения #{suggestion_id}. Предыдущие параметры восстановлены.")
        else:
            await message.answer(f"❌ Не удалось откатить изменение #{suggestion_id}. Проверьте, что запись с этим ID действительно была применена (auto_applied=1).")
    except Exception as e:
        logger.error(f"Ошибка при откате предложения: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при откате предложения: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("compound_buy:"))
async def handle_compound_buy(callback: types.CallbackQuery):
    opp_id = callback.data.split(":", 1)[1]
    from agents.shared.python.db import mark_compound_bought
    await asyncio.to_thread(mark_compound_bought, opp_id)
    await callback.answer("✅ Отмечено как куплено!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"🟢 <b>Позиция открыта.</b> Жду резолюцию рынка или Exit-сигнал.", parse_mode="HTML")

@dp.callback_query(lambda c: c.data and c.data.startswith("compound_skip:"))
async def handle_compound_skip(callback: types.CallbackQuery):
    opp_id = callback.data.split(":", 1)[1]
    from agents.shared.python.db import get_connection
    def run_db():
        with get_connection() as conn:
            conn.execute(
                "UPDATE compound_opportunities SET status='EXPIRED' WHERE id=?", (opp_id,)
            )
    await asyncio.to_thread(run_db)
    await callback.answer("❌ Пропущено")
    await callback.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(lambda c: c.data and c.data.startswith("compound_sell:"))
async def handle_compound_sell(callback: types.CallbackQuery):
    # Формат: compound_sell:{opp_id}:{exit_price}
    parts = callback.data.split(":")
    opp_id = parts[1]
    exit_price = float(parts[2])
    
    from agents.shared.python.db import get_connection, resolve_compound_opportunity, get_compound_settings
    from services.favourite_compounder import ROICalculator
    
    def run_exit_db():
        with get_connection() as conn:
            opp = conn.execute("SELECT * FROM compound_opportunities WHERE id = ?", (opp_id,)).fetchone()
            if not opp or opp["status"] != "BOUGHT":
                return None
            return dict(opp)
            
    opp = await asyncio.to_thread(run_exit_db)
    if not opp:
        await callback.answer("❌ Ошибка: позиция уже закрыта или не существует")
        return
        
    cfg = await asyncio.to_thread(get_compound_settings)
    virtual_stake = cfg.get("virtual_stake", 50.0)
    
    pnl = virtual_stake * (exit_price - opp["price"]) / opp["price"] * (1.0 - ROICalculator.POLY_FEE_PCT)
    pnl = round(pnl, 2)
    
    await asyncio.to_thread(resolve_compound_opportunity, opp_id, "YES", pnl, exit_price)
    
    await callback.answer("💎 Зафиксировано досрочное закрытие!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(f"🟢 <b>Позиция досрочно закрыта (Профи-продажа).</b>\n💰 PnL: <b>+${pnl:.2f}</b> (выход по {int(exit_price*100)}¢)", parse_mode="HTML")

@dp.message(Command("compound"))
async def cmd_compound(message: types.Message):
    """Показывает активные Favourite Compounding возможности."""
    from agents.shared.python.db import get_active_compound_opportunities, get_compound_stats
    opps = await asyncio.to_thread(get_active_compound_opportunities)
    stats = await asyncio.to_thread(get_compound_stats)
    
    lines = ["💰 <b>FAVOURITE COMPOUNDING</b>\n"]
    lines.append(
        f"📊 <b>Статистика:</b>\n"
        f"• Всего возможностей: <b>{stats['total']}</b>\n"
        f"• Открыто позиций: <b>{stats['bought']}</b>\n"
        f"• Закрыто позиций: <b>{stats['resolved']}</b>\n"
        f"• Win Rate: <b>{stats['win_rate']*100:.1f}%</b>\n"
        f"• Общий PnL: <b>${stats['total_pnl']:.2f}</b>\n\n"
        f"📈 <b>Активные сигналы:</b>"
    )
    
    if not opps:
        lines.append("<i>Активных Favourite Compounding возможностей нет.</i>")
    else:
        for o in opps[:10]:
            status_label = "🆕" if o["status"] == "NEW" else ("⏳" if o["status"] == "ALERTED" else "🟢 КУПЛЕНО")
            lines.append(
                f"• {o['title'][:40]}...\n"
                f"  {status_label} | {int(o['price']*100)}¢ | ROI <b>+{o['roi_net_pct']:.2f}%</b> | "
                f"{o['hours_left']:.1f}ч"
            )
            
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустить сканирование", callback_data="scan_favourite_compound")]
    ])
    await message.answer("\n".join(lines), reply_markup=keyboard, parse_mode="HTML")


@dp.callback_query(F.data == "scan_favourite_compound")
async def callback_scan_favourite_compound(callback: CallbackQuery) -> None:
    # Дедупликация: игнорируем повторно доставленные callback'и
    async with _callback_dedup_lock:
        is_processed = callback.id in _processed_callback_ids
        if not is_processed:
            _processed_callback_ids.append(callback.id)
            
    if is_processed:
        await callback.answer()
        return

    engine = get_core_engine()
    if _scan_lock.locked() or _penny_scan_lock.locked() or engine._scan_lock.locked() or engine._penny_scan_lock.locked():
        await callback.answer()
        await callback.message.answer("⚠️ Сейчас выполняется другое сканирование рынков. Пожалуйста, подождите.")
        return

    if _favourite_compound_lock.locked():
        await callback.answer()
        await callback.message.answer("⚠️ Сканирование Favourite Compounding уже выполняется. Пожалуйста, подождите.")
        return

    await callback.message.answer("🚀 Запущено сканирование Favourite Compounding...")
    await callback.answer("🔄 Запуск...")
    try:
        from main import scheduled_favourite_compounding
        await scheduled_favourite_compounding()
        await callback.message.answer("✅ Сканирование Favourite Compounding завершено!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при сканировании: {e}")


@dp.message(F.text)
async def conversational_handler(message: types.Message) -> None:
    sender = getattr(message.from_user, 'username', None) or getattr(message.from_user, 'id', 'unknown')
    is_forward = message.forward_origin is not None
    is_bot_msg = getattr(message.from_user, 'is_bot', False)

    logger.debug(
        f"[MSG] chat={message.chat.id} from={sender} "
        f"is_bot={is_bot_msg} is_forward={is_forward} "
        f"via_bot={message.via_bot is not None} "
        f"text_preview={message.text[:50]!r}"
    )

    # 1. Игнорируем команды
    if message.text.startswith("/"):
        return

    # 2. Фильтруем нежелательные источники сообщений
    if is_bot_msg:
        logger.info(f"[FILTERED] Бот-сообщение от {sender} пропущено")
        return
    if is_forward:
        logger.info(f"[FILTERED] Форвард от {sender} пропущен")
        return
    if message.via_bot is not None:
        logger.info(f"[FILTERED] Сообщение via bot от {sender} пропущено")
        return
    if message.from_user is None:
        logger.info(f"[FILTERED] Сообщение без живого автора в chat={message.chat.id} пропущено")
        return

    # 3. Дедупликация
    msg_key = (message.chat.id, message.message_id)
    if msg_key in _processed_message_ids:
        logger.warning(f"Дублирующееся сообщение пропущено: {msg_key}")
        return
    _processed_message_ids.append(msg_key)
        
    chat_id = message.chat.id
    user_text = message.text
    
    # Уведомляем пользователя, что бот печатает ответ
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Получаем историю чата (последние 15 сообщений для контекста)
    history = await asyncio.to_thread(get_chat_history, chat_id, 15)
    
    # Отправляем запрос к Gemini с таймаутом
    try:
        response_text = await asyncio.wait_for(
            asyncio.to_thread(ask_gemini, user_text, history),
            timeout=60.0  # 60 секунд — разумный лимит для диалога
        )
    except asyncio.TimeoutError:
        logger.warning(f"[conversational_handler] ask_gemini timeout для chat_id={chat_id}")
        await message.answer("⏱ Gemini API не ответил за 60 сек. Попробуй позже.")
        return
    
    # Не сохраняем в историю ошибки (таймаут или сбой API)
    if not response_text.startswith("Ошибка"):
        # Сохраняем сообщение пользователя и ответ в базу
        await asyncio.to_thread(save_chat_message, chat_id, "user", user_text)
        await asyncio.to_thread(save_chat_message, chat_id, "model", response_text)
        
        # Очищаем старую историю (согласно MEMORY_POLICY не храним длинные логи)
        await asyncio.to_thread(cleanup_chat_history, chat_id, 20)
    
    # Отправляем ответ пользователю по умолчанию как обычный текст во избежание ошибок парсинга HTML
    try:
        await message.answer(response_text, parse_mode=None)
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке сообщения в Telegram: {e}")

async def main() -> None:
    from config import startup_check
    startup_check()
    print("🤖 Бот NEXUS запускается...")
    await set_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
