import os
import requests
import json
import time
import logging
from typing import Optional, Tuple
from agents.shared.python.db import save_token_usage

logger = logging.getLogger("gemini_client")

def _lower_types(schema):
    """Рекурсивно приводит значения 'type' к нижнему регистру для совместимости с OpenAI."""
    if isinstance(schema, dict):
        new_schema = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                new_schema[k] = v.lower()
            else:
                new_schema[k] = _lower_types(v)
        return new_schema
    elif isinstance(schema, list):
        return [_lower_types(item) for item in schema]
    return schema

def convert_gemini_to_openai(payload: dict, model_name: str = "grok-3") -> dict:
    """
    Конвертирует payload из формата Google Gemini API в формат OpenAI (совместимый с Grok и OpenRouter).
    """
    openai_messages = []
    
    # 1. Извлекаем system instruction
    system_instruction = ""
    if "systemInstruction" in payload:
        parts = payload["systemInstruction"].get("parts", [])
        if parts:
            system_instruction = parts[0].get("text", "")
            
    if system_instruction:
        openai_messages.append({"role": "system", "content": system_instruction})
        
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
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": f"call_{fr.get('name')}", # Имитация ID
                        "name": fr.get("name"),
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
                tool_calls.append({
                    "id": f"call_{fc.get('name')}", # Имитация ID
                    "type": "function",
                    "function": {
                        "name": fc.get("name"),
                        "arguments": json.dumps(fc.get("args", {}))
                    }
                })
                
        message_dict = {"role": openai_role, "content": text_content if text_content else None}
        if tool_calls:
            message_dict["tool_calls"] = tool_calls
            
        openai_messages.append(message_dict)
        
    openai_payload = {
        "model": model_name,
        "messages": openai_messages
    }
    
    # 3. Конвертация инструментов (tools)
    if "tools" in payload:
        openai_tools = []
        for gemini_tool in payload["tools"]:
            if "functionDeclarations" in gemini_tool:
                for func in gemini_tool["functionDeclarations"]:
                    # OpenAI требует lower-case для типов, Gemini обычно UPPERCASE (OBJECT, STRING)
                    func_copy = json.loads(json.dumps(func))
                    if "parameters" in func_copy:
                        func_copy["parameters"] = _lower_types(func_copy["parameters"])
                        
                    openai_tools.append({
                        "type": "function",
                        "function": func_copy
                    })
        if openai_tools:
            openai_payload["tools"] = openai_tools
            
    # 4. Настройка формата JSON, если требуется
    gen_config = payload.get("generationConfig", {})
    if gen_config.get("response_mime_type") == "application/json":
        openai_payload["response_format"] = {"type": "json_object"}
        
    return openai_payload

def convert_openai_to_gemini(openai_res: dict) -> dict:
    """
    Конвертирует ответ из формата OpenAI API (Grok/OpenRouter) обратно в формат Gemini.
    """
    choice = openai_res["choices"][0]
    message = choice.get("message", {})
    text = message.get("content") or ""
    
    parts = []
    if text:
        parts.append({"text": text})
        
    tool_calls = message.get("tool_calls", [])
    for tc in tool_calls:
        if tc.get("type") == "function":
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except Exception:
                args = {}
                
            parts.append({
                "functionCall": {
                    "name": func.get("name"),
                    "args": args
                }
            })
            
    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
    
    return {
        "candidates": [
            {
                "content": {
                    "parts": parts,
                    "role": "model"
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens
        }
    }

def generate_content_with_fallback(
    api_key: str, 
    payload: dict, 
    default_model: str = "gemini-2.5-flash", 
    agent_name: str = "AGENT",
    timeout: int = 120
) -> Tuple[Optional[dict], str]:
    """
    Выполняет HTTP POST запрос к API с автоматической маршрутизацией.
    Если в окружении задан GROK_API_KEY, то все запросы в первую очередь направляются 
    в Grok API (модель grok-2). Модели Gemini используются как фолбек (резерв).
    
    При успешном выполнении автоматически сохраняет расход токенов в БД.
    
    :param api_key: Google API Key (используется для Gemini)
    :param payload: Тело запроса в формате Gemini API
    :param default_model: Модель Gemini по умолчанию
    :param agent_name: Имя агента для логирования токенов и ошибок
    :return: Кортеж (result_json, успешная_модель)
    """
    grok_key = os.getenv("GROK_API_KEY")
    grok_model = os.getenv("GROK_MODEL", "grok-3")
    or_key = os.getenv("OPENROUTER_API_KEY")
    or_model = os.getenv("OPENROUTER_MODEL", "openrouter/owl-alpha")
    
    # Формируем список моделей для опроса. Если есть ключ OpenRouter, он идет ПЕРВЫМ.
    models = []
    if or_key:
        models.append("openrouter")
    if grok_key:
        models.append("grok")
        
    # Добавляем модели Gemini в список
    gemini_models = [default_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"]
    for m in gemini_models:
        if m not in models:
            models.append(m)
            
    for current_model in models:
        # --- ВЕТКА OPENROUTER ---
        if current_model == "openrouter":
            if not or_key:
                continue
                
            logger.info(f"[{agent_name}] Отправка запроса в OpenRouter API (модель {or_model})...")
            try:
                openai_payload = convert_gemini_to_openai(payload, model_name=or_model)
                
                headers = {
                    "Authorization": f"Bearer {or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/orlovRoman/polimarket",
                    "X-Title": "Polymarket Bot Team"
                }
                
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=openai_payload,
                    headers=headers,
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    openai_res = response.json()
                    
                    if "error" in openai_res:
                        logger.error(f"[{agent_name}] Ошибка в ответе OpenRouter API: {openai_res['error']}")
                        continue
                        
                    if "choices" not in openai_res or not openai_res["choices"]:
                        logger.error(f"[{agent_name}] Ответ OpenRouter API не содержит choices: {openai_res}")
                        continue
                        
                    result = convert_openai_to_gemini(openai_res)
                    
                    # Сохраняем токены
                    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
                    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
                    if prompt_tokens > 0 or completion_tokens > 0:
                        try:
                            save_token_usage(agent_name, or_model, prompt_tokens, completion_tokens)
                        except Exception as e:
                            logger.error(f"[{agent_name}] Ошибка сохранения расхода токенов OpenRouter: {e}")
                            
                    return result, or_model
                else:
                    logger.error(f"[{agent_name}] Ошибка OpenRouter API ({response.status_code}): {response.text}")
            except Exception as e:
                logger.error(f"[{agent_name}] Исключение при запросе к OpenRouter: {e}")
            continue
            
        # --- ВЕТКА GROK ---
        if current_model == "grok":
            if not grok_key:
                continue
                
            logger.info(f"[{agent_name}] Отправка запроса в Grok API (модель {grok_model})...")
            try:
                openai_payload = convert_gemini_to_openai(payload, model_name=grok_model)
                
                headers = {
                    "Authorization": f"Bearer {grok_key}",
                    "Content-Type": "application/json"
                }
                
                response = requests.post(
                    "https://api.x.ai/v1/chat/completions",
                    json=openai_payload,
                    headers=headers,
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    openai_res = response.json()
                    result = convert_openai_to_gemini(openai_res)
                    
                    # Сохраняем токены
                    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
                    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
                    if prompt_tokens > 0 or completion_tokens > 0:
                        try:
                            save_token_usage(agent_name, grok_model, prompt_tokens, completion_tokens)
                        except Exception as e:
                            logger.error(f"[{agent_name}] Ошибка сохранения расхода токенов Grok: {e}")
                            
                    return result, grok_model
                else:
                    logger.error(f"[{agent_name}] Ошибка Grok API ({response.status_code}): {response.text}")
            except Exception as e:
                logger.error(f"[{agent_name}] Исключение при запросе к Grok: {e}")
            continue
            
        # --- ВЕТКА GEMINI ---
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
        
        logger.info(f"[{agent_name}] Отправка запроса в Gemini API (модель {current_model})...")
        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                result = response.json()
                
                # Логируем расход токенов
                usage_meta = result.get("usageMetadata", {})
                input_tokens = usage_meta.get("promptTokenCount", 0)
                output_tokens = usage_meta.get("candidatesTokenCount", 0)
                
                if input_tokens > 0 or output_tokens > 0:
                    try:
                        save_token_usage(agent_name, current_model, input_tokens, output_tokens)
                    except Exception as e:
                        logger.error(f"[{agent_name}] Ошибка сохранения расхода токенов для {current_model}: {e}")
                
                if "candidates" in result and result["candidates"]:
                    return result, current_model
                else:
                    logger.warning(f"[{agent_name}] Модель {current_model} вернула успешный статус 200, но без кандидатов.")
                    continue
                    
            elif response.status_code == 429:
                logger.warning(f"[{agent_name}] Ограничение лимита (429) для {current_model}. Пробуем альтернативу...")
            else:
                logger.error(f"[{agent_name}] Ошибка API ({response.status_code}) для {current_model}: {response.text}")
                
        except Exception as e:
            logger.error(f"[{agent_name}] Исключение при запросе к {current_model}: {e}")
            
        # Экспоненциальный бэкоф между попытками Gemini
        attempt = models.index(current_model) if current_model in models else 0
        backoff = min(0.5 * (2 ** attempt), 5.0)
        time.sleep(backoff)
        
    logger.error(f"[{agent_name}] Критическая ошибка: все доступные модели (Grok и Gemini) вернули ошибку.")
    return None, default_model
