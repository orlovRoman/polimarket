import os
import sys

# Настройка кодировки для Windows консоли
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.shared.python.db import init_db, save_token_usage, get_token_usage_last_24h, get_connection, get_agent_model

def test_token_logging():
    print("=== Запуск теста логирования токенов ===")
    
    # 1. Инициализируем БД
    init_db()
    
    # 2. Очистим тестовые записи от прошлых тестов для наглядности (если они есть)
    with get_connection() as conn:
        conn.execute("DELETE FROM token_usage WHERE agent_name LIKE 'TEST_%'")
        conn.commit()
    
    # 3. Сохраним тестовые записи
    print("Сохраняем тестовые данные о токенах...")
    save_token_usage("TEST_SCOUT", "gemini-2.5-flash", 1000, 200)
    save_token_usage("TEST_SCOUT", "gemini-2.5-pro", 1500, 300) # используем другую модель во 2-й записи
    save_token_usage("TEST_SHADOW", "gemini-2.5-flash", 500, 100)
    
    # 4. Проверим агрегацию за 24 часа
    scout_stats = get_token_usage_last_24h("TEST_SCOUT")
    shadow_stats = get_token_usage_last_24h("TEST_SHADOW")
    herald_stats = get_token_usage_last_24h("TEST_HERALD") # для проверки дефолтных значений
    
    print("\nРезультаты агрегации за последние 24 часа:")
    print(f"TEST_SCOUT: {scout_stats}")
    print(f"TEST_SHADOW: {shadow_stats}")
    print(f"TEST_HERALD (нет записей): {herald_stats}")
    
    # 5. Утверждения (Asserts) для токенов
    assert scout_stats['input_tokens'] == 2500, f"Ожидалось 2500, получено {scout_stats['input_tokens']}"
    assert scout_stats['output_tokens'] == 500, f"Ожидалось 500, получено {scout_stats['output_tokens']}"
    assert scout_stats['total_tokens'] == 3000, f"Ожидалось 3000, получено {scout_stats['total_tokens']}"
    
    assert shadow_stats['input_tokens'] == 500, f"Ожидалось 500, получено {shadow_stats['input_tokens']}"
    assert shadow_stats['output_tokens'] == 100, f"Ожидалось 100, получено {shadow_stats['output_tokens']}"
    assert shadow_stats['total_tokens'] == 600, f"Ожидалось 600, получено {shadow_stats['total_tokens']}"
    
    assert herald_stats['total_tokens'] == 0, f"Ожидалось 0, получено {herald_stats['total_tokens']}"
    
    # 6. Проверим динамическое определение моделей
    scout_model = get_agent_model("TEST_SCOUT")
    shadow_model = get_agent_model("TEST_SHADOW")
    herald_model_default = get_agent_model("TEST_HERALD")
    herald_model_custom = get_agent_model("TEST_HERALD", "gemini-3.5-flash")
    
    print("\nРезультаты определения моделей:")
    print(f"TEST_SCOUT модель (ожидается последняя: gemini-2.5-pro): {scout_model}")
    print(f"TEST_SHADOW модель (ожидается: gemini-2.5-flash): {shadow_model}")
    print(f"TEST_HERALD модель (ожидается дефолт: gemini-2.5-flash): {herald_model_default}")
    print(f"TEST_HERALD модель (ожидается кастомный дефолт: gemini-3.5-flash): {herald_model_custom}")
    
    assert scout_model == "gemini-2.5-pro", f"Ожидалась gemini-2.5-pro, получено {scout_model}"
    assert shadow_model == "gemini-2.5-flash", f"Ожидалась gemini-2.5-flash, получено {shadow_model}"
    assert herald_model_default == "gemini-2.5-flash", f"Ожидалась gemini-2.5-flash, получено {herald_model_default}"
    assert herald_model_custom == "gemini-3.5-flash", f"Ожидалась gemini-3.5-flash, получено {herald_model_custom}"

    # Очищаем тестовые данные после успешного теста
    with get_connection() as conn:
        conn.execute("DELETE FROM token_usage WHERE agent_name LIKE 'TEST_%'")
        conn.commit()
        
    print("\n✅ Все тесты (токены и модели) успешно пройдены!")

if __name__ == "__main__":
    test_token_logging()
