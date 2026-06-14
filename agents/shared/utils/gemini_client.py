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

logger = logging.getLogger("NexusPolyBot.gemini_client")

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

def _extract_system_instruction(payload: dict) -> Optional[dict]:
    if "systemInstruction" in payload:
        parts = payload["systemInstruction"].get("parts", [])
        if parts:
            text = parts[0].get("text", "")
            if text:
                return {"role": "system", "content": text}
    return None

def _convert_messages(contents: list) -> Tuple[list, dict]:
    openai_messages = []
    tool_call_ids = {}
    
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
        
    return openai_messages, tool_call_ids

def _convert_tools(payload: dict) -> Optional[list]:
    if "tools" not in payload:
        return None
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
    return openai_tools if openai_tools else None

def _convert_response_format(payload: dict, strict_json: bool) -> Optional[dict]:
    gen_config = payload.get("generationConfig", {})
    mime_type = gen_config.get("responseMimeType") or gen_config.get("response_mime_type")
    if mime_type == "application/json":
        if "responseSchema" in gen_config and strict_json:
            schema_copy = _lower_types(gen_config["responseSchema"])
            if isinstance(schema_copy, dict):
                schema_copy["additionalProperties"] = False
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": schema_copy,
                    "strict": True
                }
            }
        else:
            # Cerebras и другие не поддерживают strict json_schema — простой json_object
            return {"type": "json_object"}
    return None

def convert_gemini_to_openai(payload: dict, model_name: str = "", strict_json: bool = True) -> dict:
    """
    Конвертирует payload из формата Google Gemini API в формат OpenAI (совместимый с Grok и OpenRouter).
    """
    openai_messages = []
    
    # 1. Извлекаем system instruction
    sys_msg = _extract_system_instruction(payload)
    if sys_msg:
        openai_messages.append(sys_msg)
        
    # 2. Извлекаем сообщения (историю диалога)
    contents = payload.get("contents", [])
    converted_msgs, _ = _convert_messages(contents)
    openai_messages.extend(converted_msgs)
    
    openai_payload = {
        "model": model_name,
        "messages": openai_messages
    }
    
    # 3. Конвертация инструментов (tools)
    tools = _convert_tools(payload)
    if tools:
        openai_payload["tools"] = tools
        
    # 4. Настройка формата JSON, если требуется
    resp_format = _convert_response_format(payload, strict_json)
    if resp_format:
        openai_payload["response_format"] = resp_format
        
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
    
    FINISH_REASON_MAP = {
        "stop": "STOP", "length": "MAX_TOKENS",
        "content_filter": "SAFETY", "tool_calls": "STOP",
    }
    finish_reason = FINISH_REASON_MAP.get(choice.get("finish_reason", ""), "STOP")
    
    return {
        "candidates": [
            {
                "content": {
                    "parts": parts,
                    "role": "model"
                },
                "finishReason": finish_reason
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

def _is_safe_char(ch: str) -> bool:
    cp = ord(ch)
    # Разрешаем: printable ASCII + Unicode + таб/новая строка/возврат каретки
    # Блокируем: C0 (0-31 кроме \t (9), \n (10), \r (13)), DEL (127), C1 (128-159)
    if cp in (9, 10, 13):
        return True
    if cp < 32 or cp == 127:
        return False
    if 128 <= cp <= 159:
        return False
    return True

def _sanitize_payload_strings(data, parent_key=None):
    """Рекурсивно очищает строки в payload от null bytes, управляющих символов и обрезает их при превышении лимита."""
    if isinstance(data, dict):
        return {k: _sanitize_payload_strings(v, parent_key=k) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_payload_strings(item, parent_key=parent_key) for item in data]
    elif isinstance(data, str):
        # Валидация числовых полей конфигурации генерации, переданных в виде строк
        if parent_key in ("temperature", "topP"):
            try:
                val = float(data)
                logger.warning(f"[GEMINI_CLIENT] Поле {parent_key} передано как строка, конвертируем в float: {val}")
                return val
            except ValueError:
                pass
        elif parent_key in ("maxOutputTokens", "topK"):
            try:
                val = int(data)
                logger.warning(f"[GEMINI_CLIENT] Поле {parent_key} передано как строка, конвертируем в int: {val}")
                return val
            except ValueError:
                pass

        # 1. Убираем null bytes и управляющие символы (C0, DEL, C1)
        cleaned = "".join(ch for ch in data if _is_safe_char(ch))
        # 2. Обрезка текста (безопасный лимит ~80k символов)
        MAX_CONTEXT_CHARS = 80_000
        if len(cleaned) > MAX_CONTEXT_CHARS:
            logger.warning(
                f"[GEMINI_CLIENT] Текст в payload превышает {MAX_CONTEXT_CHARS} символов и будет обрезан (исходная длина: {len(cleaned)})"
            )
            cleaned = cleaned[:MAX_CONTEXT_CHARS] + "\n\n[...контекст обрезан...]"
        return cleaned
    return data

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
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 400:
            logger.error(f"[GEMINI] 400 Bad Request Details: {response.text[:1000]}")
        raise e
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
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 400:
            logger.error(f"[OPENROUTER] 400 Bad Request Details: {response.text[:1000]}")
        raise e
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
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 400:
            logger.error(f"[CEREBRAS] 400 Bad Request Details: {response.text[:1000]}")
        raise e
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
        "models": [
            os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            "nvidia/nemotron-3-super-120b-a12b:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "google/gemma-4-31b-it:free",
            "openai/gpt-oss-120b:free",
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free"
        ],
        "send_func": _send_openrouter
    },
    "cerebras": {
        "keys": [os.getenv("CEREBRAS_API_KEY", "")],
        # Актуальные модели по состоянию на 06.2026 (см. https://api.cerebras.ai/public/v1/models):
        # qwen-3-235b-a22b-instruct-2507, llama3.1-8b, llama-3.3-70b — удалены с платформы
        "models": ["gpt-oss-120b", "zai-glm-4.7"],
        "send_func": _send_cerebras
    }
}

_rate_lock = Lock()
_rate_limits: dict[str, int] = {
    "openrouter": 20,
    # zai-glm-4.7: 500 RPM, gpt-oss-120b: 1000 RPM (06.2026, Unicorn plan)
    # Используем минимальное значение. Примечание: Cerebras может применять
    # rate limit как 1 RPS, поэтому не превышаем 500.
    "cerebras": 500,
}
_request_times: dict[str, list[float]] = {
    "gemini": [], "openrouter": [], "cerebras": []
}

def _rate_limit_wait(provider: str = "gemini", num_keys: int = 1):
    """Блокирует выполнение если превышен лимит запросов в минуту."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
        
    if provider == "gemini":
        max_rpm = max(num_keys * 6, 6)
    else:
        max_rpm = _rate_limits.get(provider, 12)
        
    with _rate_lock:
        now = time.monotonic()
        times = _request_times.setdefault(provider, [])
        times[:] = [t for t in times if now - t < 60]
        if len(times) >= max_rpm:
            wait = 60 - (now - times[0]) + 1
            logger.info(f"[rate_limiter:{provider}] Пауза {wait:.1f}с (лимит {max_rpm} RPM)")
            time.sleep(wait)
        times.append(time.monotonic())

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

def _is_payment_required_error(e: Exception) -> bool:
    """402 Payment Required — ключ бесплатный, модель платная.
    Обрабатываем аналогично 404: пропускаем модель, не пробуем другие ключи для неё.
    """
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        if e.response.status_code == 402:
            return True
    err_str = str(e).lower()
    if "402" in err_str or "payment required" in err_str:
        return True
    return False


_GEMINI_KEY_ENV_NAMES = [
    "GOOGLE_API_KEY",
    "GOOGLE_API_KEY_SECONDARY",
    "GOOGLE_API_KEY_THIRD",
]

def _collect_gemini_keys(primary_key: str) -> list[str]:
    """Собирает все Gemini ключи в детерминированном порядке."""
    keys = []
    if primary_key and primary_key.strip():
        keys.append(primary_key)
    for name in _GEMINI_KEY_ENV_NAMES[1:]:
        val = os.getenv(name, "")
        if val and val.strip() and val not in keys:
            keys.append(val)
    return keys


_failure_lock = Lock()

def _increment_failure(agent_name: str) -> int:
    """Атомарно увеличивает счетчик ошибок для агента с использованием блокировки."""
    from agents.shared.python.db import get_memory, save_memory
    fail_key = f"consecutive_failures_{agent_name}"
    with _failure_lock:
        failures = int(get_memory(fail_key) or 0) + 1
        save_memory(fail_key, failures)
        return failures


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
    import random
    
    # Снимаем один snapshot индексов ротации, чтобы избежать race conditions
    cer_rr_idx_snapshot = int(get_memory("cer_rr_index") or 0)
    gem_rr_idx_snapshot = int(get_memory("gem_rr_index") or 0)
    gem_key_rr_idx_snapshot = int(get_memory("gem_key_rr_index") or 0)
    or_rr_idx_snapshot = int(get_memory("or_rr_index") or 0)

    if "PYTEST_CURRENT_TEST" not in os.environ:
        time.sleep(random.uniform(0, 2.0))

    # Санитизация строк в payload перед отправкой (убираем null bytes, управляющие символы и ограничиваем размер)
    payload = _sanitize_payload_strings(payload)

    # Настраиваем тайм-аут по умолчанию динамически
    if timeout == 30:
        TIMEOUT_MAP = {
            "gemini-2.5-pro": 90,
            "gemini-2.5-flash": 45,
        }
        timeout = TIMEOUT_MAP.get(default_model.lower() if default_model else "", 30)

    # Строим рабочий словарь providers явно (без deepcopy, чтобы MagicMock в тестах работал).
    # Ключи gemini: если PROVIDERS_CONFIG["gemini"]["keys"] непустой (патч в тестах),
    # используем его напрямую; иначе строим из api_key + secondary из env.
    _gemini_keys_override = PROVIDERS_CONFIG["gemini"].get("keys") or []
    if _gemini_keys_override:
        _gemini_keys = list(_gemini_keys_override)
    else:
        # Собираем все Gemini ключи: первичный + все вторичные из переменных окружения динамически
        all_keys = _collect_gemini_keys(api_key)
        
        # RR по ключам: начинаем с текущего
        _gemini_keys = all_keys[gem_key_rr_idx_snapshot:] + all_keys[:gem_key_rr_idx_snapshot]
        
    _active_gemini_keys_for_rr = _gemini_keys

    # Разделяем дефолтную модель по провайдерам, чтобы не слать некорректные модели в Gemini API
    is_gemini_model = default_model.startswith("gemini-") or default_model in PROVIDERS_CONFIG["gemini"]["models"]
    # Покрываем все известные Cerebras-префиксы + динамическую проверку по списку моделей
    _CEREBRAS_PREFIXES = ("qwen", "gpt-oss", "zai-", "llama")
    is_cerebras_model = (
        any(default_model.startswith(p) for p in _CEREBRAS_PREFIXES)
        or default_model in PROVIDERS_CONFIG["cerebras"]["models"]
    )

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
                ([default_model] if (not is_gemini_model and not is_cerebras_model) else []) + list(PROVIDERS_CONFIG["openrouter"]["models"])
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

    # Cerebras: добавляем ВСЕ модели с ротацией (аналогично Gemini),
    # чтобы при ошибке первой модели фоллбэк шёл на следующие Cerebras-модели,
    # а не сразу переключался на другой провайдер.
    if "cerebras" in active_providers:
        cer_models = providers["cerebras"]["models"]
        if len(cer_models) > 0:
            save_memory("cer_rr_index", (cer_rr_idx_snapshot + 1) % len(cer_models))
        cer_rotated = cer_models[cer_rr_idx_snapshot % max(len(cer_models), 1):] + cer_models[:cer_rr_idx_snapshot % max(len(cer_models), 1)]
        cer_seen: set = set()
        for _cm in cer_rotated:
            if _cm not in cer_seen:
                cer_seen.add(_cm)
                plans.append(("cerebras", _cm))

    # OpenRouter
    if "openrouter" in active_providers:
        or_models = providers["openrouter"]["models"]
        agent_override = os.getenv(f"OPENROUTER_MODEL_{agent_name.upper()}")
        if len(or_models) > 0:
            save_memory("or_rr_index", (or_rr_idx_snapshot + 1) % len(or_models))
        if agent_override:
            plans.append(("openrouter", agent_override))
        else:
            or_rotated = or_models[or_rr_idx_snapshot % max(len(or_models), 1):] + or_models[:or_rr_idx_snapshot % max(len(or_models), 1)]
            or_seen = set()
            for _om in or_rotated:
                if _om not in or_seen:
                    or_seen.add(_om)
                    plans.append(("openrouter", _om))

    # Gemini
    if "gemini" in active_providers:
        gem_models = providers["gemini"]["models"]
        if len(gem_models) > 0:
            save_memory("gem_rr_index", (gem_rr_idx_snapshot + 1) % len(gem_models))
            
        save_memory("gem_key_rr_index", (gem_key_rr_idx_snapshot + 1) % max(len(_active_gemini_keys_for_rr), 1))
        
        # Строим список: начинаем с текущего индекса, идём по кругу
        idx = gem_rr_idx_snapshot % max(len(gem_models), 1)
        rotated = gem_models[idx:] + gem_models[:idx]
        
        seen = set()
        for m in rotated:
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
                    if not db_model:
                        db_model = "gemini_round_robin"
                    
                    if db_model == "gemini_round_robin":
                        gem_models_cfg = providers["gemini"]["models"]
                        resolved_model = gem_models_cfg[gem_rr_idx_snapshot % len(gem_models_cfg)]
                        gemini_plans = [("gemini", resolved_model)]
                    else:
                        seen = set()
                        gemini_plans = []
                        for m in [db_model] + providers["gemini"]["models"]:
                            if m not in seen:
                                seen.add(m)
                                gemini_plans.append(("gemini", m))
                    
                    plans = gemini_plans + [p for p in plans if p[0] != "gemini"]
                elif prov_override == "openrouter":
                    if not db_model:
                        db_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
                    if db_model.lower().startswith("gemini-"):
                        logger.warning(f"[{agent_name}] Модель {db_model} несовместима с OpenRouter, сброс на дефолт")
                        db_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
                    plans = [p for p in plans if p[0] != "openrouter"]
                    plans.insert(0, ("openrouter", db_model))
                elif prov_override == "cerebras":
                    plans = [p for p in plans if p[0] != "cerebras"]
                    if not db_model:
                        db_model = "cerebras_round_robin"
                    if db_model == "cerebras_round_robin":
                        cer_models = providers["cerebras"]["models"]
                        resolved_model = cer_models[cer_rr_idx_snapshot % len(cer_models)]
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
            _rate_limit_wait(provider, num_keys=len(keys))
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
                if _is_rate_limit_error(e):
                    logger.warning(
                        f"[{agent_name}] 429 Rate Limit на {provider} ({model}) ключ {key_idx+1}. Переходим к следующему ключу."
                    )
                    if provider == "cerebras":
                        _cer_idx_now = int(get_memory("cer_rr_index") or 0)
                        cer_models = providers["cerebras"]["models"]
                        save_memory("cer_rr_index", (_cer_idx_now + 1) % len(cer_models))
                    elif provider == "gemini":
                        k_idx = int(get_memory("gem_key_rr_index") or 0)
                        save_memory("gem_key_rr_index", (k_idx + 1) % max(len(_active_gemini_keys_for_rr), 1))
                    continue
                
                # Если это 404 (Model Not Found) — сразу выходим из попыток и переходим к следующей модели
                if _is_model_not_found_error(e):
                    logger.error(f"[{agent_name}] 404: модель {model} на {provider} не существует или удалена. Пропускаем.")
                    if provider == "cerebras":  # двигаем RR при 404 тоже
                        _cer_idx_now = int(get_memory("cer_rr_index") or 0)
                        cer_models = providers["cerebras"]["models"]
                        save_memory("cer_rr_index", (_cer_idx_now + 1) % len(cer_models))
                    skip_model = True
                    break
                
                # 402 Payment Required — ошибка ключа (не модели!).
                # Обе Cerebras-модели бесплатные (Unicorn plan), поэтому 402 = ключ истёк/неверен.
                # Пропускаем текущую модель; следующие тоже получат 402 — это нормально,
                # они быстро отсеются и управление перейдёт к следующему провайдеру.
                if _is_payment_required_error(e):
                    key_env = {
                        "cerebras": "CEREBRAS_API_KEY",
                        "openrouter": "OPENROUTER_API_KEY",
                        "gemini": "GOOGLE_API_KEY",
                    }.get(provider, f"{provider.upper()}_API_KEY")
                    logger.error(
                        f"[{agent_name}] 402 Payment Required от {provider} ({model}): "
                        f"проверьте {key_env} или модель (возможно нужен :free суффикс). Пропускаем."
                    )
                    skip_model = True
                    break
                
                # Для других ошибок - не делаем retry, переходим к следующему ключу
                logger.warning(f"[{agent_name}] Ошибка вызова {provider} ({model}) с ключом {key_idx+1}: {error_msg}")
                if provider == "cerebras":
                    if isinstance(e, requests.exceptions.HTTPError):
                        _cer_idx_now = int(get_memory("cer_rr_index") or 0)
                        cer_models = providers["cerebras"]["models"]
                        save_memory("cer_rr_index", (_cer_idx_now + 1) % len(cer_models))
                
            
            
    # 5. Если все провайдеры, модели и ключи дали ошибку
    logger.error(f"[{agent_name}] Критическая ошибка: все доступные модели и ключи вернули ошибку.")
    
    failures = _increment_failure(agent_name)
    
    # Уведомляем по экспоненциальной шкале (3, 10, 30, 90...), не сбрасывая failures в 0 сразу
    if failures in (3, 10, 30, 90, 270):
        logger.warning(f"[{agent_name}] Достигнут лимит последовательных ошибок ({failures}). Отправка уведомления.")
        if agent_name.upper() != "GROUNDING":
            from services.notifications import send_telegram
            send_telegram(
                text=f"⚠️ <b>СБОЙ LLM-МОДЕЛЕЙ И КЛЮЧЕЙ</b>\n\nАгент <b>{agent_name}</b> не смог получить ответ ни от одной модели или ключа {failures} раза подряд.\nВыберите другую модель:",
                reply_markup={"inline_keyboard": [[{"text": f"🔄 Сменить модель для {agent_name}", "callback_data": f"set_model_{agent_name}"}]]}
            )
        
    from core.guards import LLMUnavailableError
    raise LLMUnavailableError(f"Все модели и ключи LLM вернули ошибку для агента {agent_name}.", agent_name=agent_name)
