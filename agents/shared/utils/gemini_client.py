import os
import requests
import json
import uuid
import time
import re
import logging
from threading import Lock
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
        
    tool_calls = message.get("tool_calls") or []
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
            
    if not parts:
        parts.append({"text": ""})
        
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
        parts = result['candidates'][0]['content']['parts']
        if not parts:
            return ""
        return parts[0].get('text', '')
    except Exception as e:
        MAX_ERR_DUMP = 500
        raw = json.dumps(result)
        raise ValueError(f"Не удалось извлечь текст ответа. API вернуло: {raw[:MAX_ERR_DUMP]}{'...' if len(raw) > MAX_ERR_DUMP else ''}") from e

def _send_gemini(payload: dict, model: str, api_key: str, timeout: int) -> Tuple[dict, int, int]:
    """Отправка запроса напрямую в Google Gemini API."""
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gemini_payload = json.loads(json.dumps(payload))
    if "tools" in gemini_payload and "generationConfig" in gemini_payload:
        gen_cfg = gemini_payload["generationConfig"]
        if "responseMimeType" in gen_cfg or "response_mime_type" in gen_cfg:
            gen_cfg.pop("responseMimeType", None)
            gen_cfg.pop("response_mime_type", None)
            gen_cfg.pop("responseSchema", None)
            gen_cfg.pop("response_schema", None)
            
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json"
    }
    response = requests.post(api_url, json=gemini_payload, headers=headers, timeout=timeout)
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
        # Порядок: default_model (передаётся в аргументах) препендится до этих с дедупликацией
        "models": [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ],
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

_rate_lock = Lock()
_request_times: list[float] = []
MAX_REQUESTS_PER_MINUTE = 12  # 2 ключа × ~6 rpm на ключ

def _rate_limit_wait():
    """Блокирует выполнение если превышен лимит запросов в минуту."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    with _rate_lock:
        now = time.monotonic()
        # Убираем запросы старше 60 секунд
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= MAX_REQUESTS_PER_MINUTE:
            wait = 60 - (now - _request_times[0]) + 1
            logger.info(f"[rate_limiter] Квота близка, пауза {wait:.1f}с")
            time.sleep(wait)
        _request_times.append(time.monotonic())

def _sanitize_error(e: Exception) -> str:
    """Маскирует API ключи в тексте ошибок перед логированием."""
    return re.sub(r'key=[A-Za-z0-9_\-\.]{20,}', 'key=***REDACTED***', str(e))


def _is_rate_limit_error(e: Exception) -> bool:
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        if e.response.status_code == 429:
            return True
    err_str = str(e).lower()
    if "429" in err_str or "rate limit" in err_str or "rate_limit" in err_str or "too many requests" in err_str:
        return True
    return False

def _is_model_not_found_error(e: Exception) -> bool:
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        if e.response.status_code == 404:
            return True
    err_str = str(e).lower()
    if "model not found" in err_str or "model_not_found" in err_str:
        return True
    return False


def generate_content_with_fallback(
    api_key: str,
    payload: dict,
    default_model: str = "gemini-2.5-flash",
    agent_name: str = "AGENT",
    timeout: int = 30,
    market_id: Optional[str] = None
) -> Tuple[Optional[dict], str]:
    """
    Выполняет HTTP POST запрос к API с автоматической маршрутизацией по провайдерам,
    моделям и ключам с поддержкой автоматического переключения при ошибках.
    """
    from agents.shared.python.db import get_memory, save_memory

    # Настраиваем тайм-аут по умолчанию динамически
    if timeout == 30:
        model_key = default_model.lower() if default_model else ""
        if "gemini-2.5-pro" in model_key:
            timeout = 90
        elif "gemini-2.5-flash" in model_key:
            timeout = 45
        else:
            timeout = 30

    # Строим рабочий словарь providers явно (без deepcopy, чтобы MagicMock в тестах работал).
    # Ключи gemini: если PROVIDERS_CONFIG["gemini"]["keys"] непустой (патч в тестах),
    # используем его напрямую; иначе строим из api_key + secondary из env.
    _gemini_keys_override = PROVIDERS_CONFIG["gemini"].get("keys") or []
    if _gemini_keys_override:
        _gemini_keys = list(_gemini_keys_override)
    else:
        secondary = os.getenv("GOOGLE_API_KEY_SECONDARY", "")
        if not secondary:
            logger.debug("[gemini_client] GOOGLE_API_KEY_SECONDARY не задан в .env")
        _gemini_keys = [k for k in [api_key, secondary] if k and k.strip()]

    # Разделяем дефолтную модель по провайдерам, чтобы не слать некорректные модели в Gemini API
    is_gemini_model = default_model.startswith("gemini-") or default_model in PROVIDERS_CONFIG["gemini"]["models"]
    is_cerebras_model = default_model.startswith("qwen") or default_model in PROVIDERS_CONFIG["cerebras"]["models"]

    providers = {
        "gemini": {
            "keys": _gemini_keys,
            "models": list(dict.fromkeys(
                ([default_model] if is_gemini_model else []) + list(PROVIDERS_CONFIG["gemini"]["models"])
            )),
            "send_func": PROVIDERS_CONFIG["gemini"]["send_func"],
        },
        "openrouter": {
            "keys": [k for k in PROVIDERS_CONFIG["openrouter"]["keys"] if k and k.strip()],
            "models": list(dict.fromkeys(
                ([default_model] if (not is_gemini_model and not is_cerebras_model) else []) + [os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")]
            )),
            "send_func": PROVIDERS_CONFIG["openrouter"]["send_func"],
        },
        "cerebras": {
            "keys": [k for k in PROVIDERS_CONFIG["cerebras"]["keys"] if k and k.strip()],
            "models": list(dict.fromkeys(
                ([default_model] if is_cerebras_model else []) + list(PROVIDERS_CONFIG["cerebras"]["models"])
            )),
            "send_func": PROVIDERS_CONFIG["cerebras"]["send_func"],
        },
    }
    
    # 1. Фильтруем провайдеров, оставляя только тех, у кого заданы API-ключи
    active_providers = []
    for prov_name, cfg in providers.items():
        cfg["keys"] = [k for k in cfg["keys"] if k and k.strip()]
        if cfg["keys"]:
            active_providers.append(prov_name)
        else:
            # БАГ 3 ФИКС: логируем выпадение провайдера (чтобы не дебажить в продакшне)
            if prov_name not in ("gemini",):  # gemini собирает ключи динамически, пустые здесь нормальны
                logger.debug(f"[{agent_name}] Провайдер '{prov_name}' пропущен: API-ключ не задан в окружении")

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
                    if db_model and db_model.lower().startswith("gemini-"):
                        logger.warning(f"[{agent_name}] Модель {db_model} несовместима с OpenRouter, сброс на дефолт")
                        db_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
                    plans = [p for p in plans if p[0] != "openrouter"]
                    plans.insert(0, ("openrouter", db_model))
                elif prov_override == "cerebras":
                    plans = [p for p in plans if p[0] != "cerebras"]
                    if not db_model:
                        db_model = "cerebras_round_robin"
                    if db_model == "cerebras_round_robin":
                        cer_idx = int(get_memory("cer_rr_index", 0))
                        cer_models = providers["cerebras"]["models"]
                        resolved_model = cer_models[cer_idx % len(cer_models)]
                        plans.insert(0, ("cerebras", resolved_model))
                    else:
                        plans.insert(0, ("cerebras", db_model))
    except Exception as e:
        logger.error(f"Error reading model config for {agent_name}: {e}")

    prompt_text = extract_prompt_from_payload(payload)

    # 4. Перебираем plans и ключи
    last_provider = None
    for provider, model in plans:
        if last_provider and last_provider != provider:
            logger.info(f"[{agent_name}] Переключение провайдера: {last_provider} -> {provider} (модель {model})")
        last_provider = provider
        cfg = providers[provider]
        send_func = cfg["send_func"]
        keys = cfg["keys"]
        
        skip_model = False
        for key_idx, key in enumerate(keys):
            if skip_model:
                break
            for attempt in range(3):
                _rate_limit_wait()
                start_time = time.time()
                logger.info(f"[{agent_name}] Отправка запроса в {provider} (модель {model}, ключ {key_idx+1}/{len(keys)}, попытка {attempt+1}/3)...")
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
                        cer_models = providers["cerebras"]["models"]
                        save_memory("cer_rr_index", (cer_idx + 1) % len(cer_models))
                        
                    save_memory(f"consecutive_failures_{agent_name}", 0)
                    return result, model
                    
                except Exception as e:
                    latency_ms = int((time.time() - start_time) * 1000)
                    error_msg = _sanitize_error(e)
                    
                    # Логируем вызов с ошибкой
                    LLMLogger.log_call(
                        agent_name, model, prompt_text, error=f"Key {key_idx+1} Error: {error_msg}",
                        latency_ms=latency_ms, market_id=market_id
                    )
                    
                    # Проверяем тип ошибки
                    if _is_rate_limit_error(e) and attempt < 2:
                        wait = 2 ** attempt  # 1s, 2s, 4s
                        logger.warning(
                            f"[{agent_name}] 429 Rate Limit на {provider} ({model}) ключ {key_idx+1}, "
                            f"backoff {wait}s (попытка {attempt+1}/3)"
                        )
                        time.sleep(wait)
                        # Продолжаем внутренний цикл attempt для повторной попытки
                        continue
                    
                    # Если это 404 (Model Not Found) — сразу выходим из попыток и переходим к следующей модели
                    if _is_model_not_found_error(e):
                        logger.error(f"[{agent_name}] 404: модель {model} на {provider} не существует или удалена. Пропускаем.")
                        if provider == "cerebras":  # двигаем RR при 404 тоже
                            _cer_idx_now = int(get_memory("cer_rr_index", 0))
                            cer_models = providers["cerebras"]["models"]
                            save_memory("cer_rr_index", (_cer_idx_now + 1) % len(cer_models))
                        skip_model = True
                        break
                    
                    # Для других ошибок (или при исчерпании попыток 429) - не делаем retry, переходим к следующему ключу
                    logger.warning(f"[{agent_name}] Ошибка вызова {provider} ({model}) с ключом {key_idx+1}: {error_msg}")
                    if provider == "cerebras":
                        if isinstance(e, requests.exceptions.HTTPError):
                            _cer_idx_now = int(get_memory("cer_rr_index", 0))
                            cer_models = providers["cerebras"]["models"]
                            save_memory("cer_rr_index", (_cer_idx_now + 1) % len(cer_models))
                    break  # прерываем цикл попыток attempt для текущего ключа
                
            
            
    # 5. Если все провайдеры, модели и ключи дали ошибку
    logger.error(f"[{agent_name}] Критическая ошибка: все доступные модели и ключи вернули ошибку.")
    
    fail_key = f"consecutive_failures_{agent_name}"
    failures = int(get_memory(fail_key) or 0) + 1
    save_memory(fail_key, failures)
    
    # Уведомляем по экспоненциальной шкале (3, 10, 30, 90...), не сбрасывая failures в 0 сразу
    if failures in (3, 10, 30, 90, 270):
        logger.warning(f"[{agent_name}] Достигнут лимит последовательных ошибок ({failures}). Отправка уведомления.")
        from services.notifications import send_telegram
        send_telegram(
            text=f"⚠️ <b>СБОЙ LLM-МОДЕЛЕЙ И КЛЮЧЕЙ</b>\n\nАгент <b>{agent_name}</b> не смог получить ответ ни от одной модели или ключа {failures} раза подряд.\nВыберите другую модель:",
            reply_markup={"inline_keyboard": [[{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]]}
        )
        
    from core.guards import LLMUnavailableError
    raise LLMUnavailableError(f"Все модели и ключи LLM вернули ошибку для агента {agent_name}.", agent_name=agent_name)
