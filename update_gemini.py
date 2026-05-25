import re

with open('agents/shared/utils/gemini_client.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'import uuid' not in content:
    content = content.replace('import json', 'import json\nimport uuid')

replacement = '''    # Словарь для маппинга tool_call_id
    tool_call_ids = {}

    # 2. Извлекаем сообщения (историю диалога)
    contents = payload.get("contents", [])
    for msg in contents:
        role = msg.get("role", "user")
        parts = msg.get("parts", [])
        
        # В Gemini ответы инструментов имеют роль 'function'
        if role == "function":
            for part in parts:
                if "functionResponse" in part:
                    fr = part["functionResponse"]
                    name = fr.get("name")
                    # Достаем ID из словаря (первый встречный)
                    t_id = tool_call_ids.get(name, []).pop(0) if tool_call_ids.get(name) else f"call_{name}_{uuid.uuid4().hex[:4]}"
                    
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": t_id,
                        "name": name,
                        "content": json.dumps(fr.get("response", {}))
                    })
            continue

        # В Gemini роль для ответов модели - 'model', в OpenAI - 'assistant'
        openai_role = "assistant" if role in ["model", "assistant"] else "user"
        
        text_content = ""
        tool_calls = []
        
        for part in parts:
            if "text" in part:
                text_content += part["text"]
            elif "functionCall" in part:
                fc = part["functionCall"]
                name = fc.get("name")
                t_id = f"call_{name}_{uuid.uuid4().hex[:8]}"
                if name not in tool_call_ids:
                    tool_call_ids[name] = []
                tool_call_ids[name].append(t_id)
                
                tool_calls.append({
                    "id": t_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })'''

pattern = re.compile(r'    # 2\. Извлекаем сообщения.*?\}\)\n', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(replacement + '\n', content)
    with open('agents/shared/utils/gemini_client.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Updated gemini_client.py')
else:
    print('Pattern not found!')
