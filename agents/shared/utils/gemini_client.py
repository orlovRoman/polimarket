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

def convert_gemini_to_openai(payload: dict, model_name: str = "", strict_json: bool = True) -> dict:
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

def _send_gemini(payload: dict, model: str, api_key: str, timeout: int) -> Tuple[dict, int, int]:
    """Отправка запроса напрямую в Google Gemini API."""
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    gemini_payload = json.loads(json.dumps(payload))
    if "tools" in gemini_payload and "generationConfig" in gemini_payload:
        gen_cfg = gemini_payload["generationConfig"]
        if "responseMimeType" in gen_cfg or "response_mime_type" in gen_cfg:
            gen_cfg.pop("responseMimeType", None)
            gen_cfg.pop("response_mime_type", None)
            gen_cfg.pop("responseSchema", None)
            gen_cfg.pop("response_schema", None)
            
    response = requests.post(api_url, json=gemini_payload, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if "candidates" not in result or not result["candidates"]:
        raise ValueError("No candidates in response")
        
    usage_meta = result.get("usageMetadata", {})
    input_tokens = usage_meta.get("promptTokenCount", 0)
    output_tokens = usage_meta.get("candidatesTokenCount", 0)
    return result, input_tokens, output_tokens

def _send_openrouter(payload: dict, model: str, api_key: str, timeout: int) -> Tuple[dict, int, int]:
    """Отправка запроса в OpenRouter API (конвертация в формат OpenAI)."""
    openai_payload = convert_gemini_to_openai(payload, model_name=model)
    headers = {
        "Authorization": f"Bearer {api_key}",
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
    response.raise_for_status()
    openai_res = response.json()
    if "error" in openai_res:
        raise ValueError(str(openai_res["error"]))
    if "choices" not in openai_res or not openai_res["choices"]:
        raise ValueError("No choices in response")
        
    result = convert_openai_to_gemini(openai_res)
    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
    return result, prompt_tokens, completion_tokens

def _send_cerebras(payload: dict, model: str, api_key: str, timeout: int) -> Tuple[dict, int, int]:
    """Отправка запроса в Cerebras API (конвертация в формат OpenAI, без строгого json)."""
    openai_payload = convert_gemini_to_openai(payload, model_name=model, strict_json=False)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    response = requests.post(
        "https://api.cerebras.ai/v1/chat/completions",
        json=openai_payload,
        headers=headers,
        timeout=timeout
    )
    if response.status_code == 429:
        CEREBRAS_RATE_LIMIT_WAIT_SEC = 20
        logger.warning(f"Ошибка 429 от Cerebras ({model}). Ждем {CEREBRAS_RATE_LIMIT_WAIT_SEC} секунд...")
        time.sleep(CEREBRAS_RATE_LIMIT_WAIT_SEC)
    response.raise_for_status()
    openai_res = response.json()
    result = convert_openai_to_gemini(openai_res)
    prompt_tokens = openai_res.get("usage", {}).get("prompt_tokens", 0)
    completion_tokens = openai_res.get("usage", {}).get("completion_tokens", 0)
    return result, prompt_tokens, completion_tokens


# Реестр провайдеров и конфигурация.
# Вынесен на уровень модуля для тестируемости (можно патчить в тестах).
# Для добавления 3-го провайдера достаточно прописать его ключ, модели и функцию-обработчик здесь.
# ВАЖНО: ключи gemini['keys'] формируются динамически внутри generate_content_with_fallback,
# чтобы prepend-ить первичный api_key, переданный в аргументах.
PROVIDERS_CONFIG: dict = {
    "gemini": {
        # keys будут собраны динамически (prepend api_key + secondary из env)
        "keys": [],
        "models": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        "send_func": _send_gemini
    },
    "openrouter": {
        "keys": [os.getenv("OPENROUTER_API_KEY", "")],
        "models": [os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")],
        "send_func": _send_openrouter
    },
    "cerebras": {
        "keys": [os.getenv("CEREBRAS_API_KEY", "")],
        "models": ["qwen-3-235b-a22b-instruct-2507", "gpt-oss-120b", "zai-glm-4.7", "llama3.1-8b"],
        "send_func": _send_cerebras
    }
}


def generate_content_with_fallback(
    api_key: str,
    payload: dict,
    default_model: str = "gemini-2.5-flash",
    agent_name: str = "AGENT",
    timeout: int = 120,
    market_id: Optional[str] = None
) -> Tuple[Optional[dict], str]:
    """
    Выполняет HTTP POST запрос к API с автоматической маршрутизацией по провайдерам,
    моделям и ключам с поддержкой автоматического переключения при ошибках.
    """
    from agents.shared.python.db import get_memory, save_memory

    # Строим рабочий словарь providers явно (без deepcopy, чтобы MagicMock в тестах работал).
    # Ключи gemini: если PROVIDERS_CONFIG["gemini"]["keys"] непустой (патч в тестах),
    # используем его напрямую; иначе строим из api_key + secondary из env.
    _gemini_keys_override = PROVIDERS_CONFIG["gemini"].get("keys") or []
    if _gemini_keys_override:
        _gemini_keys = list(_gemini_keys_override)
    else:
        secondary = os.getenv("GOOGLE_API_KEY_SECONDARY", "AIzaSyByIvR_9P2sj74EkN8mxWU5-VC4koRwIFM")
        _gemini_keys = [k for k in [api_key, secondary] if k and k.strip()]

    providers = {
        "gemini": {
            "keys": _gemini_keys,
            "models": [default_model] + list(PROVIDERS_CONFIG["gemini"]["models"]),
            "send_func": PROVIDERS_CONFIG["gemini"]["send_func"],
        },
        "openrouter": {
            "keys": list(PROVIDERS_CONFIG["openrouter"]["keys"]),
            "models": list(PROVIDERS_CONFIG["openrouter"]["models"]),
            "send_func": PROVIDERS_CONFIG["openrouter"]["send_func"],
        },
        "cerebras": {
            "keys": list(PROVIDERS_CONFIG["cerebras"]["keys"]),
            "models": list(PROVIDERS_CONFIG["cerebras"]["models"]),
            "send_func": PROVIDERS_CONFIG["cerebras"]["send_func"],
        },
    }
    
    # 1. Фильтруем провайдеров, оставляя только тех, у кого заданы API-ключи
    active_providers = []
    for prov_name, cfg in providers.items():
        cfg["keys"] = [k for k in cfg["keys"] if k and k.strip()]
        if cfg["keys"]:
            active_providers.append(prov_name)

    # 2. Формируем список планов исполнения: (provider, model)
    plans = []

    # Cerebras
    if "cerebras" in active_providers:
        cer_idx = int(get_memory("cer_rr_index", 0))
        cer_models = providers["cerebras"]["models"]
        cer_model = cer_models[cer_idx % len(cer_models)]
        plans.append(("cerebras", cer_model))

    # OpenRouter
    if "openrouter" in active_providers:
        or_model_default = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        or_model = os.getenv(f"OPENROUTER_MODEL_{agent_name.upper()}", or_model_default)
        plans.append(("openrouter", or_model))

    # Gemini
    if "gemini" in active_providers:
        seen = set()
        for m in providers["gemini"]["models"]:
            if m not in seen:
                seen.add(m)
                plans.append(("gemini", m))

    # 3. Учитываем ручную настройку моделей из БД
    try:
        config_db = get_memory(f"agent_config_{agent_name.upper()}")
        if config_db and isinstance(config_db, dict):
            prov_override = config_db.get("provider")
            db_model = config_db.get("model")
            if prov_override in active_providers:
                if prov_override == "gemini":
                    plans = [p for p in plans if p[0] != "gemini"]
                    seen = set()
                    for m in [db_model] + providers["gemini"]["models"]:
                        if m not in seen:
                            seen.add(m)
                            plans.insert(0, ("gemini", m))
                elif prov_override == "openrouter":
                    plans = [p for p in plans if p[0] != "openrouter"]
                    plans.insert(0, ("openrouter", db_model))
                elif prov_override == "cerebras":
                    plans = [p for p in plans if p[0] != "cerebras"]
                    plans.insert(0, ("cerebras", db_model))
    except Exception as e:
        logger.error(f"Error reading model config for {agent_name}: {e}")

    prompt_text = extract_prompt_from_payload(payload)
    gemini_attempt = 0

    # 4. Перебираем планы и ключи
    for provider, model in plans:
        cfg = providers[provider]
        send_func = cfg["send_func"]
        keys = cfg["keys"]
        
        for key_idx, key in enumerate(keys):
            start_time = time.time()
            logger.info(f"[{agent_name}] Отправка запроса в {provider} (модель {model}, ключ {key_idx+1}/{len(keys)})...")
            try:
                result, in_tokens, out_tokens = send_func(payload, model, key, timeout)
                
                # Успешный запрос!
                latency_ms = int((time.time() - start_time) * 1000)
                total_tokens = in_tokens + out_tokens
                response_text = extract_response_text(result)
                
                LLMLogger.log_call(
                    agent_name, model, prompt_text, response=response_text,
                    input_tokens=in_tokens, output_tokens=out_tokens, total_tokens=total_tokens,
                    latency_ms=latency_ms, market_id=market_id
                )
                
                # Обновляем round-robin для Cerebras при успехе
                if provider == "cerebras":
                    cer_idx = int(get_memory("cer_rr_index", 0))
                    save_memory("cer_rr_index", cer_idx + 1)
                    
                save_memory(f"consecutive_failures_{agent_name}", 0)
                return result, model
                
            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                error_msg = str(e)
                logger.warning(f"[{agent_name}] Ошибка вызова {provider} ({model}) с ключом {key_idx+1}: {error_msg}")
                LLMLogger.log_call(
                    agent_name, model, prompt_text, error=f"Key {key_idx+1} Error: {error_msg}",
                    latency_ms=latency_ms, market_id=market_id
                )
                # Переходим к следующему ключу этого же провайдера
                continue
                
        # Если это был Gemini и мы прошлись по всем ключам, делаем экспоненциальный бэкоф перед следующей моделью
        if provider == "gemini":
            backoff = min(0.5 * (2 ** gemini_attempt), 5.0)
            time.sleep(backoff)
            gemini_attempt += 1
            
    # 5. Если все провайдеры, модели и ключи дали ошибку
    logger.error(f"[{agent_name}] Критическая ошибка: все доступные модели и ключи вернули ошибку.")
    
    fail_key = f"consecutive_failures_{agent_name}"
    failures = int(get_memory(fail_key) or 0) + 1
    save_memory(fail_key, failures)
    
    if failures >= 3:
        logger.warning(f"[{agent_name}] Достигнут лимит последовательных ошибок ({failures}). Отправка уведомления.")
        from services.notifications import send_telegram
        send_telegram(
            text=f"⚠️ <b>СБОЙ LLM-МОДЕЛЕЙ И КЛЮЧЕЙ</b>\n\nАгент <b>{agent_name}</b> не смог получить ответ ни от одной модели или ключа {failures} раза подряд.\nВыберите другую модель:",
            reply_markup={"inline_keyboard": [[{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]]}
        )
        save_memory(fail_key, 0)
        
    from core.guards import LLMUnavailableError
    raise LLMUnavailableError(f"Все модели и ключи LLM вернули ошибку для агента {agent_name}.", agent_name=agent_name)
