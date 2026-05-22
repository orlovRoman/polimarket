import os
import requests
import json
import time
from typing import Optional, Tuple
from agents.shared.python.db import save_token_usage

def generate_content_with_fallback(
    api_key: str, 
    payload: dict, 
    default_model: str = "gemini-2.5-flash", 
    agent_name: str = "AGENT",
    timeout: int = 30
) -> Tuple[Optional[dict], str]:
    """
    Выполняет HTTP POST запрос к Gemini REST API с поддержкой fallback-ротации моделей.
    Если базовая модель возвращает ошибку (например, 429 Too Many Requests), 
    система последовательно опрашивает альтернативные модели.
    
    При успешном выполнении автоматически сохраняет расход токенов в БД.
    
    :param api_key: Google API Key
    :param payload: Тело запроса (contents, generationConfig и др.)
    :param default_model: Модель по умолчанию
    :param agent_name: Имя агента для логирования токенов и ошибок
    :return: Кортеж (result_json, успешная_модель) или (None, default_model)
    """
    # Собираем приоритетный список моделей для ротации
    models = [default_model]
    alternatives = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-flash-latest", "gemini-2.5-pro"]
    
    for alt in alternatives:
        if alt not in models:
            models.append(alt)
            
    for current_model in models:
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
        
        # Обновляем имя модели в payloads или заголовках, если необходимо (для REST API Gemini имя модели передается в URL, так что payload менять не нужно)
        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
            
            if response.status_code == 200:
                result = response.json()
                
                # Логируем расход токенов для реально сработавшей модели
                usage_meta = result.get("usageMetadata", {})
                input_tokens = usage_meta.get("promptTokenCount", 0)
                output_tokens = usage_meta.get("candidatesTokenCount", 0)
                
                if input_tokens > 0 or output_tokens > 0:
                    try:
                        save_token_usage(agent_name, current_model, input_tokens, output_tokens)
                    except Exception as e:
                        print(f"[{agent_name}] Ошибка сохранения расхода токенов для {current_model}: {e}")
                
                # Проверяем наличие кандидатов в ответе
                if "candidates" in result and result["candidates"]:
                    return result, current_model
                else:
                    print(f"[{agent_name}] Модель {current_model} вернула успешный статус 200, но без кандидатов.")
                    continue
                    
            elif response.status_code == 429:
                print(f"[{agent_name}] Ограничение лимита (429) для {current_model}. Пробуем альтернативу...")
            else:
                print(f"[{agent_name}] Ошибка API ({response.status_code}) для {current_model}: {response.text}")
                
        except Exception as e:
            print(f"[{agent_name}] Исключение при запросе к {current_model}: {e}")
            
        # Экспоненциальный бэкоф между попытками
        attempt = models.index(current_model) if current_model in models else 0
        backoff = min(0.5 * (2 ** attempt), 5.0)
        time.sleep(backoff)
        
    print(f"[{agent_name}] Критическая ошибка: все доступные модели Gemini вернули ошибку.")
    return None, default_model
