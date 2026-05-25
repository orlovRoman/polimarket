import os
import sys
from datetime import datetime
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.append(str(Path(__file__).parent.parent))

from agents.shared.utils.obsidian_adapter import ObsidianAdapter
from agents.shared.python.db import update_vault_index, get_connection, init_db
from core.models import Market
from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
from agents.shared.utils.rag import get_rag_context, search_memories

def test_rag_and_double_blind():
    print("=== ЗАПУСК ТЕСТА RAG И DOUBLE-BLIND ===")
    
    # 0. Инициализируем БД
    init_db()
    
    # 1. Создаем тестовую заметку в Obsidian через адаптер
    adapter = ObsidianAdapter()
    category = "durable"
    filename = "test-rag-scout-trump"
    content = "Этот тестовый файл содержит информацию о том, что вероятность победы Дональда Трампа на выборах оценивается в 65% из-за сильной поддержки в ключевых штатах."
    tags = ["trump", "election", "politics"]
    
    print("\n[RAG] Создаем тестовую заметку...")
    filepath = adapter.promote_to_memory(category, filename, content, tags)
    print(f"[RAG] Заметка создана: {filepath}")
    
    # Путь относительно корня vault
    rel_path = str(filepath.relative_to(adapter.vault_path))
    
    # Явно индексируем в БД (на всякий случай)
    import hashlib
    content_hash = hashlib.md5(content.encode()).hexdigest()
    update_vault_index(rel_path, category, filename, tags, content_hash)
    print(f"[RAG] Заметка проиндексирована в SQLite под путем: {rel_path}")
    
    # 2. Тестируем RAG-поиск
    print("\n[RAG] Тестируем поиск по ключевым словам...")
    search_results = search_memories("Donald Trump election details", "politics event", limit=1)
    
    if not search_results:
        print("❌ Ошибка: Тестовая заметка не найдена через RAG!")
        cleanup(rel_path, filepath)
        return
        
    found_note = search_results[0]
    print(f"✅ Успех! Заметка найдена с релевантностью {found_note['score']:.1f}")
    print(f"  Найденные ключевые слова: {found_note['matched_keywords']}")
    
    context = get_rag_context("Will Trump win the elections?", "Checking politics outcome")
    print("\n=== ОТФОРМАТИРОВАННЫЙ RAG КОНТЕКСТ ===")
    print(context)
    print("=======================================")
    
    # 3. Тестируем Double-Blind для ScoutAgent
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        print("\n⚠️ Предупреждение: GOOGLE_API_KEY не найден в .env. Пропуск теста вызова LLM.")
        cleanup(rel_path, filepath)
        return
        
    print("\n[SCOUT] Тестируем независимую оценку в режиме Double-Blind...")
    scout = ScoutAgent(api_key=key)
    
    # Создаем фиктивный рынок с ценой 0.40 (40%)
    market = Market(
        id="test-mkt-trump-1",
        platform="polymarket",
        title="Will Donald Trump win the next US Presidential Election?",
        description="This market resolves to YES if Donald Trump wins the US Presidential Election.",
        url="https://polymarket.com/event/trump-win",
        outcome="YES",
        price=0.40,
        close_time=datetime.now()
    )
    
    print(f"Анализируем рынок: {market.title}")
    print(f"Рыночная цена: {market.price} (вероятность {market.price * 100}%) — Скрыта от ИИ!")
    
    signal = scout.estimate_market(market)
    
    if signal:
        print("\n✅ Успех! Scout нашел Edge и сгенерировал сигнал:")
        print(f"  Сигнал ID: {signal.id}")
        print(f"  Математическое преимущество (Edge): {signal.edge*100:.1f}%")
        print(f"  Оценка вероятности Scout: {scout_probability_from_details(signal.edge, market.price)*100:.1f}%")
        print(f"  Доверие: {signal.confidence}")
        print(f"  Резюме: {signal.summary}")
        print(f"  Обоснование (с RAG-памятью):\n{signal.details}")
    else:
        print("\n--- Scout не нашел Edge (оценка близка к 40% или ниже), либо произошла ошибка API.")
        
    cleanup(rel_path, filepath)

def scout_probability_from_details(edge: float, market_price: float) -> float:
    # Метод для обратного вычисления вероятности на основе Edge (для вывода в лог)
    # edge = est_prob - market.price => est_prob = edge + market.price
    return edge + market_price

def cleanup(rel_path: str, filepath: Path):
    print("\n[CLEANUP] Удаляем тестовые данные...")
    # Удаляем запись из индекса SQLite
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM vault_index WHERE path = ?", (rel_path,))
            conn.commit()
        print("✅ Запись в SQLite удалена.")
    except Exception as e:
        print(f"Ошибка удаления записи в SQLite: {e}")
        
    # Удаляем файл
    if filepath.exists():
        filepath.unlink()
        print("✅ Тестовый файл удален с диска.")
        
    print("=== ТЕСТ ЗАВЕРШЕН ===")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    test_rag_and_double_blind()
