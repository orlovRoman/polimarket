import re

with open('telegram/bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'BaseMiddleware' not in content:
    content = content.replace('from aiogram import Bot, Dispatcher, types, F', 'from aiogram import Bot, Dispatcher, types, F, BaseMiddleware')

middleware_code = """class AuthMiddleware(BaseMiddleware):
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

        return await handler(event, data)"""

content = re.sub(r'def _is_stale_message.*?return chat_id == AUTHORIZED_CHAT_ID\n', middleware_code.strip() + '\n', content, flags=re.DOTALL)

if 'dp.message.middleware(AuthMiddleware())' not in content:
    content = content.replace('dp = Dispatcher()', 'dp = Dispatcher()\ndp.message.middleware(AuthMiddleware())\ndp.callback_query.middleware(AuthMiddleware())')

content = re.sub(r'^\s*if _is_stale_message\(.*?\):?\s*return.*?\n', '', content, flags=re.MULTILINE)
content = re.sub(r'^\s*if not _is_authorized\(.*?\):?\s*return.*?\n', '', content, flags=re.MULTILINE)

with open('telegram/bot.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Middleware implemented successfully!')
