import os
import requests
import json
import uuid
import time
import logging
from typing import Optional, Tuple
from agents.shared.python.db import save_memory, get_memory

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

def convert_gemini_to_openai(payload: dict, model_name: str = "grok-3", strict_json: bool = True) -> dict:
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
        
    # Словарь для маппинга tool_call_id
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
                })

        message_dict = {"role": openai_role, "content": text_content or ""}
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
    if gen_config.get("responseMimeType") == "application/json" or gen_config.get("response_mime_type") == "application/json":
        if "responseSchema" in gen_config and strict_json:
            schema_copy = _lower_types(gen_config["responseSchema"])
            if isinstance(schema_copy, dict):
                schema_copy["additionalProperties"] = False
            openai_payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": schema_copy,
                    "strict": True
                }
            }
        else:
            # Cerebras и другие провайдеры не поддерживают strict json_schema — используем простой json_object
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

from core.logger import LLMLogger

def extract_prompt_from_payload(payload: dict) -> str:
    try:
        return payload.get("contents", [])[0].get("parts", [])[0].get("text", "")
    except Exception:
        MAX_PROMPT_LOG_CHARS = 2000
        raw = json.dumps(payload)
        return raw[:MAX_PROMPT_LOG_CHARS] + ("..." if len(raw) > MAX_PROMPT_LOG_CHARS else "")

def extract_response_text(result: dict) -> str:
    try:
        return result['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        MAX_ERR_DUMP = 500
        raw = json.dumps(result)
        raise ValueError(f"Не удалось извлечь текст ответа. API вернуло: {raw[:MAX_ERR_DUMP]}{'...' if len(raw) > MAX_ERR_DUMP else ''}") from e

def generate_content_with_fallback(
    api_key: str, 
    payload: dict, 
    default_model: str = "gemini-2.5-flash", 
    agent_name: str = "AGENT",
    timeout: int = 120,
    market_id: Optional[str] = None
) -> Tuple[Optional[dict], str]:
    """
    Выполняет HTTP POST запрос к API с автоматической маршрутизацией.
    """
    or_key = os.getenv("OPENROUTER_API_KEY")
    or_model_default = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    or_model = os.getenv(f"OPENROUTER_MODEL_{agent_name.upper()}", or_model_default)
    
    models = []
    
    cer_key = os.getenv("CEREBRAS_API_KEY")
    cer_models = [
        "qwen-3-235b-a22b-instruct-2507",
        "gpt-oss-120b",
        "zai-glm-4.7",
        "llama3.1-8b"
    ]
    
    if cer_key:
        models.append("cerebras")
    if or_key:
        models.append("openrouter")
        
    gemini_models = [default_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"]
    for m in gemini_models:
        if m not in models:
            models.append(m)
            
    # Чтение ручной настройки из БД
    try:
        from agents.shared.python.db import get_memory
        config = get_memory(f"agent_config_{agent_name.upper()}")
        if config and isinstance(config, dict):
            provider = config.get("provider")
            db_model = config.get("model")
            if provider == "openrouter":
                or_model = db_model
                if "openrouter" in models: models.remove("openrouter")
                models.insert(0, "openrouter")
            elif provider == "cerebras":
                # db_model can be ignored for cerebras since we cycle through cer_models anyway
                if "cerebras" in models: models.remove("cerebras")
                models.insert(0, "cerebras")
            elif provider == "gemini":
                default_model = db_model
                if db_model in models: models.remove(db_model)
                models.insert(0, db_model)
    except Exception as e:
        logger.error(f"Error reading model config for {agent_name}: {e}")
            
    prompt_text = extract_prompt_from_payload(payload)

    gemini_attempt = 0
    for current_model in models:
        start_time = time.time()
        
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
                
                latency_ms = int((time.time() - start_time) * 1000)
                if response.status_code == 200:
                    openai_res = response.json()
                    
                    if "error" in openai_res:
                        logger.error(f"[{agent_name}] Ошибка в ответе OpenRouter API: {openai_res['error']}")
                        LLMLogger.log_call(agent_name, or_model, prompt_text, error=str(openai_res['error']), latency_ms=latency_ms, market_id=market_id)
                        continue
                        
                    if "choices" not in openai_res or not openai_res["choices"]:
                        logger.error(f"[{agent_name}] Ответ OpenRouter API не содержит choices: {openai_res}")
                        LLMLogger.log_call(agent_name, or_model, prompt_text, error="No choices", latency_ms=latency_ms, market_id=market_id)
                        continue
                        
                    result = convert_openai_to_gemini(openai_res)
                    
                    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
                    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
                    total_tokens = prompt_tokens + completion_tokens
                    
                    response_text = extract_response_text(result)
                    LLMLogger.log_call(
                        agent_name, or_model, prompt_text, response=response_text,
                        input_tokens=prompt_tokens, output_tokens=completion_tokens, total_tokens=total_tokens,
                        latency_ms=latency_ms, market_id=market_id
                    )
                            
                    save_memory(f"consecutive_failures_{agent_name}", 0)
                    return result, or_model
                else:
                    logger.error(f"[{agent_name}] Ошибка OpenRouter API ({response.status_code}): {response.text}")
                    LLMLogger.log_call(agent_name, or_model, prompt_text, error=f"HTTP {response.status_code}: {response.text}", latency_ms=latency_ms, market_id=market_id)
            except Exception as e:
                logger.error(f"[{agent_name}] Исключение при запросе к OpenRouter: {e}")
                latency_ms = int((time.time() - start_time) * 1000)
                LLMLogger.log_call(agent_name, or_model, prompt_text, error=str(e), latency_ms=latency_ms, market_id=market_id)
            continue
            
        # --- ВЕТКА CEREBRAS ---
        if current_model == "cerebras":
            if not cer_key:
                continue
                
            cer_idx = int(get_memory("cer_rr_index", 0))
            
            for i in range(len(cer_models)):
                cer_model = cer_models[(cer_idx + i) % len(cer_models)]
                logger.info(f"[{agent_name}] Отправка запроса в Cerebras API (модель {cer_model})...")
                cer_start_time = time.time()
                try:
                    # strict_json=False: Cerebras не поддерживает OpenAI json_schema strict mode
                    openai_payload = convert_gemini_to_openai(payload, model_name=cer_model, strict_json=False)
                    
                    headers = {
                        "Authorization": f"Bearer {cer_key}",
                        "Content-Type": "application/json"
                    }
                    
                    response = requests.post(
                        "https://api.cerebras.ai/v1/chat/completions",
                        json=openai_payload,
                        headers=headers,
                        timeout=timeout
                    )
                    
                    latency_ms = int((time.time() - cer_start_time) * 1000)
                    if response.status_code == 200:
                        openai_res = response.json()
                        result = convert_openai_to_gemini(openai_res)
                        
                        prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
                        completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
                        total_tokens = prompt_tokens + completion_tokens
                        
                        response_text = extract_response_text(result)
                        LLMLogger.log_call(
                            agent_name, cer_model, prompt_text, response=response_text,
                            input_tokens=prompt_tokens, output_tokens=completion_tokens, total_tokens=total_tokens,
                            latency_ms=latency_ms, market_id=market_id
                        )
                        
                        save_memory(f"consecutive_failures_{agent_name}", 0)
                        save_memory("cer_rr_index", cer_idx + 1)
                        return result, cer_model
                    elif response.status_code == 429:
                        CEREBRAS_RATE_LIMIT_WAIT_SEC = 20
                        logger.warning(f"[{agent_name}] Ошибка 429 от Cerebras ({cer_model}). Ждем {CEREBRAS_RATE_LIMIT_WAIT_SEC} секунд сброса лимита...")
                        time.sleep(CEREBRAS_RATE_LIMIT_WAIT_SEC)
                        logger.error(f"[{agent_name}] Ошибка Cerebras API ({response.status_code}) для {cer_model}: {response.text}")
                        LLMLogger.log_call(agent_name, cer_model, prompt_text, error=f"HTTP {response.status_code}: {response.text}", latency_ms=latency_ms, market_id=market_id)
                    else:
                        logger.error(f"[{agent_name}] Ошибка Cerebras API ({response.status_code}) для {cer_model}: {response.text}")
                        LLMLogger.log_call(agent_name, cer_model, prompt_text, error=f"HTTP {response.status_code}: {response.text}", latency_ms=latency_ms, market_id=market_id)
                except Exception as e:
                    logger.error(f"[{agent_name}] Исключение при запросе к Cerebras ({cer_model}): {e}")
                    latency_ms = int((time.time() - cer_start_time) * 1000)
                    LLMLogger.log_call(agent_name, cer_model, prompt_text, error=str(e), latency_ms=latency_ms, market_id=market_id)
            continue
            
        # --- ВЕТКА GEMINI ---
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
        
        logger.info(f"[{agent_name}] Отправка запроса в Gemini API (модель {current_model})...")
        try:
            # Копируем payload, чтобы не модифицировать его для других провайдеров в цикле
            gemini_payload = json.loads(json.dumps(payload))
            if "tools" in gemini_payload and "generationConfig" in gemini_payload:
                gen_cfg = gemini_payload["generationConfig"]
                if "responseMimeType" in gen_cfg or "response_mime_type" in gen_cfg:
                    logger.info(f"[{agent_name}] Обнаружены tools. Удаляем responseMimeType из generationConfig для Gemini...")
                    gen_cfg.pop("responseMimeType", None)
                    gen_cfg.pop("response_mime_type", None)
                    gen_cfg.pop("responseSchema", None)
                    gen_cfg.pop("response_schema", None)
            
            response = requests.post(api_url, json=gemini_payload, timeout=timeout)
            latency_ms = int((time.time() - start_time) * 1000)
            
            if response.status_code == 200:
                result = response.json()
                
                usage_meta = result.get("usageMetadata", {})
                input_tokens = usage_meta.get("promptTokenCount", 0)
                output_tokens = usage_meta.get("candidatesTokenCount", 0)
                total_tokens = input_tokens + output_tokens
                
                if "candidates" in result and result["candidates"]:
                    response_text = extract_response_text(result)
                    LLMLogger.log_call(
                        agent_name, current_model, prompt_text, response=response_text,
                        input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens,
                        latency_ms=latency_ms, market_id=market_id
                    )
                    save_memory(f"consecutive_failures_{agent_name}", 0)
                    return result, current_model
                else:
                    logger.warning(f"[{agent_name}] Модель {current_model} вернула успешный статус 200, но без кандидатов.")
                    LLMLogger.log_call(agent_name, current_model, prompt_text, error="No candidates", latency_ms=latency_ms, market_id=market_id)
                    continue
                    
            elif response.status_code == 429:
                logger.warning(f"[{agent_name}] Ограничение лимита (429) для {current_model}. Пробуем альтернативу...")
                LLMLogger.log_call(agent_name, current_model, prompt_text, error="HTTP 429", latency_ms=latency_ms, market_id=market_id)
            else:
                logger.error(f"[{agent_name}] Ошибка API ({response.status_code}) для {current_model}: {response.text}")
                LLMLogger.log_call(agent_name, current_model, prompt_text, error=f"HTTP {response.status_code}: {response.text}", latency_ms=latency_ms, market_id=market_id)
                
        except Exception as e:
            logger.error(f"[{agent_name}] Исключение при запросе к {current_model}: {e}")
            latency_ms = int((time.time() - start_time) * 1000)
            LLMLogger.log_call(agent_name, current_model, prompt_text, error=str(e), latency_ms=latency_ms, market_id=market_id)
            
        # Экспоненциальный бэкоф между попытками Gemini
        backoff = min(0.5 * (2 ** gemini_attempt), 5.0)
        time.sleep(backoff)
        gemini_attempt += 1
        
    logger.error(f"[{agent_name}] Критическая ошибка: все доступные модели вернули ошибку.")
    
    # Обработка длительной недоступности
    fail_key = f"consecutive_failures_{agent_name}"
    failures = int(get_memory(fail_key) or 0) + 1
    save_memory(fail_key, failures)
    
    if failures >= 3:
        logger.warning(f"[{agent_name}] Достигнут лимит последовательных ошибок ({failures}). Отправка уведомления.")
        from services.notifications import send_telegram
        send_telegram(
            text=f"⚠️ <b>СБОЙ LLM-МОДЕЛИ</b>\n\nАгент <b>{agent_name}</b> не смог получить ответ от моделей {failures} раза подряд.\nВозможно, модель недоступна или исчерпан лимит запросов.\nВыберите другую модель:",
            reply_markup={"inline_keyboard": [[{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]]}
        )
        save_memory(fail_key, 0) # Сброс после уведомления, чтобы не спамить
        
    return None, default_model
