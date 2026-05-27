import sys
import re

with open('agents/shared/python/db.py', 'r', encoding='utf-8') as f:
    text = f.read()

new_func = """def save_agent_episode(
    agent_name: str,
    event_type: str,
    summary: str,
    market_id: str = None,
    market_title: str = None,
    context=None,
    outcome: str = "unknown"
) -> int:
    import json
    if isinstance(context, dict):
        context_str = json.dumps(context, ensure_ascii=False)
    elif isinstance(context, str):
        try:
            json.loads(context)
            context_str = context
        except (json.JSONDecodeError, TypeError):
            context_str = json.dumps(context)
    else:
        context_str = json.dumps({})

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO agent_episodes
            (agent_name, event_type, market_id, market_title, summary, context, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            agent_name, event_type, market_id, market_title,
            summary, context_str, outcome
        ))
        return cursor.lastrowid"""

text = re.sub(r'def save_agent_episode\(.*?return cursor\.lastrowid', new_func, text, flags=re.DOTALL)

with open('agents/shared/python/db.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed save_agent_episode')
