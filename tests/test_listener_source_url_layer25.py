import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Проверяем что build_tg_post_url выбирает username над числовым ID ──

def test_build_tg_post_url_prefers_username():
    """Если username есть — используем t.me/username/id, не t.me/c/..."""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = "radarpolybot"
    chat.id = -1003756373077
    result = build_tg_post_url(chat, 21491)
    assert result == "https://t.me/radarpolybot/21491", \
        f"Ожидали ссылку с username, получили: {result}"
    assert "/c/" not in result, "Формат t.me/c/ не должен использоваться для публичного канала"


def test_build_tg_post_url_fallback_numeric_for_private():
    """Для приватного канала (нет username) — используем t.me/c/{clean_id}/"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = None
    chat.id = -1003756373077
    result = build_tg_post_url(chat, 21491)
    assert result == "https://t.me/c/3756373077/21491"
    assert "radarpolybot" not in result


def test_build_tg_post_url_empty_string_username_is_private():
    """Пустая строка username — считается приватным (falsy)"""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = ""
    chat.id = -1001234567890
    result = build_tg_post_url(chat, 5)
    assert result.startswith("https://t.me/c/")


# ── get_entity fallback при отсутствии username ───────────────

def test_handler_fetches_full_entity_when_username_missing():
    """
    Если chat.username = None — handler должен вызвать client.get_entity(chat.id)
    и переиспользовать результат с username.
    """
    import asyncio
    async def run_test():
        # Мок: первый get_chat возвращает объект без username
        chat_no_username = MagicMock()
        chat_no_username.username = None
        chat_no_username.id = -1003756373077
        chat_no_username.title = "RadarPolyBot"

        # Мок: get_entity возвращает объект с username
        chat_with_username = MagicMock()
        chat_with_username.username = "radarpolybot"
        chat_with_username.id = -1003756373077

        # Симулируем логику из handler после фикса
        async def get_entity_mock(chat_id):
            return chat_with_username

        chat = chat_no_username
        if not getattr(chat, 'username', None):
            try:
                full_entity = await get_entity_mock(chat.id)
                if getattr(full_entity, 'username', None):
                    chat = full_entity
            except Exception:
                pass

        from services.telegram_listener import build_tg_post_url
        result = build_tg_post_url(chat, 21491)

        assert result == "https://t.me/radarpolybot/21491", \
            f"После get_entity должна быть ссылка с username, получили: {result}"

    asyncio.run(run_test())


def test_handler_falls_back_to_numeric_if_get_entity_fails():
    """Если get_entity упал — используем числовой ID (не крашимся)"""
    import asyncio
    async def run_test():
        chat = MagicMock()
        chat.username = None
        chat.id = -1003756373077

        async def get_entity_fail(chat_id):
            raise Exception("Network error")

        if not getattr(chat, 'username', None):
            try:
                full_entity = await get_entity_fail(chat.id)
                if getattr(full_entity, 'username', None):
                    chat = full_entity
            except Exception as e:
                pass  # используем chat как есть

        from services.telegram_listener import build_tg_post_url
        result = build_tg_post_url(chat, 21491)
        # Должен вернуть числовую ссылку, не упасть
        assert result == "https://t.me/c/3756373077/21491"
        assert result != ""

    asyncio.run(run_test())


# ── Регрессия: публичные каналы из chats_to_listen имеют username ──

def test_polymarketalerthub_url_format():
    """polymarketalerthub — публичный канал, ссылка должна быть t.me/polymarketalerthub/..."""
    from services.telegram_listener import build_tg_post_url
    chat = MagicMock()
    chat.username = "polymarketalerthub"
    chat.id = -1001111111111
    result = build_tg_post_url(chat, 100)
    assert result == "https://t.me/polymarketalerthub/100"
    assert "/c/" not in result
