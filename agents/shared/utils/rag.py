import os
import re
import json
from typing import List, Dict, Any
from pathlib import Path

from agents.shared.utils.obsidian_adapter import ObsidianAdapter
from agents.shared.python.db import get_connection

def extract_keywords(text: str) -> List[str]:
    """
    Извлекает значимые ключевые слова из текста для RAG-поиска.
    Приводит к нижнему регистру, очищает от спецсимволов и удаляет стоп-слова.
    """
    if not text:
        return []
    
    # Регулярное выражение для поиска слов (русских и английских) и цифр от 3 символов
    words = re.findall(r'[a-zA-Zа-яА-ЯёЁ0-9]{3,}', text.lower())
    
    # Расширенный список стоп-слов
    stop_words = {
        # Английские стоп-слова
        'the', 'and', 'for', 'with', 'from', 'this', 'that', 'will', 'have', 'your', 'about', 'been', 'would', 'should',
        'what', 'which', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how', 'than', 'then', 'them', 'they', 'their',
        'price', 'market', 'volume', 'trade', 'polymarket', 'predict', 'outcome', 'event', 'share', 'shares', 'yes', 'no',
        # Русские стоп-слова
        'это', 'как', 'для', 'что', 'или', 'был', 'была', 'было', 'были', 'его', 'ее', 'её', 'их', 'они', 'этого', 'тому',
        'рынок', 'цена', 'объем', 'сделка', 'полимаркет', 'исход', 'событие', 'акции', 'да', 'нет', 'прогноз', 'вероятность',
        'будет', 'быть', 'если', 'тоже', 'очень', 'после', 'через', 'перед', 'около', 'между', 'чтобы', 'хотя', 'когда'
    }
    
    keywords = []
    seen = set()
    for w in words:
        if w not in stop_words and w not in seen:
            seen.add(w)
            keywords.append(w)
            
    return keywords[:8]  # Возвращаем максимум 8 ключевых слов для высокого качества совпадений

def search_memories(market_title: str, market_description: str = "", limit: int = 2) -> List[Dict[str, Any]]:
    """
    Выполняет поиск релевантных заметок в Obsidian vault.
    Ранжирует результаты на основе пересечения ключевых слов с заголовками, тегами и текстом файлов.
    """
    adapter = ObsidianAdapter()
    combined_text = f"{market_title} {market_description or ''}"
    keywords = extract_keywords(combined_text)
    
    if not keywords:
        return []
    
    # 1. Загружаем список всех проиндексированных файлов из vault_index
    indexed_files = []
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT path, category, title, tags FROM vault_index")
            indexed_files = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"[RAG] Ошибка чтения vault_index из БД: {e}")
        return []
        
    scored_results = []
    
    for item in indexed_files:
        path_str = item['path']
        title = item['title'] or ""
        tags_raw = item['tags'] or "[]"
        category = item['category'] or "general"
        
        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
            
        # Читаем содержимое Markdown-файла
        content = adapter.read_file(path_str)
        if not content:
            continue
            
        score = 0
        matched_keywords = []
        
        # Очищаем и приводим к нижнему регистру для поиска
        title_lower = title.lower()
        content_lower = content.lower()
        tags_lower = [t.lower() for t in tags]
        
        for kw in keywords:
            kw_matched = False
            
            # Совпадение с заголовком (высокий вес)
            if kw in title_lower:
                score += 10
                kw_matched = True
                
            # Совпадение с тегами (высокий вес)
            for tag in tags_lower:
                if kw in tag:
                    score += 8
                    kw_matched = True
                    
            # Совпадение с текстом (средний вес: 1.5 очка за вхождение, макс 5 вхождений на слово)
            occurrences = len(re.findall(re.escape(kw), content_lower))
            if occurrences > 0:
                score += min(occurrences, 5) * 1.5
                kw_matched = True
                
            if kw_matched:
                matched_keywords.append(kw)
                
        # Бонус за совпадение нескольких разных ключевых слов
        if len(matched_keywords) > 1:
            score += len(matched_keywords) * 3.0
            
        # Весовые коэффициенты для категорий памяти
        if category == "durable":
            score *= 1.25  # Приоритет долгосрочным инсайтам
        elif category == "market-patterns":
            score *= 1.15  # Приоритет рыночным паттернам
        elif category == "daily":
            score *= 0.90  # Небольшое понижение для ежедневных логов
            
        if score > 0:
            # Очищаем контент от YAML frontmatter для передачи в LLM
            clean_content = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    clean_content = parts[2].strip()
                    
            scored_results.append({
                "path": path_str,
                "category": category,
                "title": title,
                "tags": tags,
                "content": clean_content,
                "score": score,
                "matched_keywords": matched_keywords
            })
            
    # Сортируем результаты по убыванию очков
    scored_results.sort(key=lambda x: x['score'], reverse=True)
    
    return scored_results[:limit]

def get_rag_context(market_title: str, market_description: str = "") -> str:
    """
    Возвращает отформатированную строку с RAG-контекстом для внедрения в промпт ИИ.
    """
    from agents.shared.python.db import get_memory
    
    # Считываем уровень RAG-анализа (1, 2 или 3) из БД. По умолчанию 2 (Стандартный)
    try:
        rag_level = get_memory("rag_level")
        if rag_level is not None:
            rag_level = int(rag_level)
        else:
            rag_level = 2
    except Exception:
        rag_level = 2
        
    # Настраиваем лимиты в зависимости от уровня
    if rag_level == 1:
        limit = 2
        max_lines = 15
        level_str = "БЫСТРЫЙ (L1)"
    elif rag_level == 3:
        limit = 8
        max_lines = 60
        level_str = "ГЛУБОКИЙ (L3)"
    else:
        limit = 4
        max_lines = 30
        level_str = "СТАНДАРТНЫЙ (L2)"
        
    try:
        results = search_memories(market_title, market_description, limit=limit)
    except Exception as e:
        print(f"[RAG] Критическая ошибка при поиске памяти: {e}")
        return "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"
        
    if not results:
        return "В базе знаний Obsidian нет релевантных записей для этого рынка.\n"
        
    context_lines = [f"=== ИЗВЛЕЧЕННАЯ ПАМЯТЬ ИЗ ОБСИДИАН (RAG - Режим: {level_str}) ==="]
    for i, res in enumerate(results, 1):
        context_lines.append(
            f"Заметка {i}: {res['title']} (Категория: {res['category']}, Релевантность: {res['score']:.1f})"
        )
        if res['tags']:
            context_lines.append(f"Теги: {', '.join(res['tags'])}")
            
        # Усекаем слишком длинные заметки во избежание переполнения контекста
        content_lines = res['content'].split('\n')
        truncated = '\n'.join(content_lines[:max_lines])
        if len(content_lines) > max_lines:
            truncated += f"\n[...содержимое заметки усечено до {max_lines} строк для экономии токенов...]"
            
        context_lines.append(f"Содержимое:\n{truncated}\n")
        
    return '\n'.join(context_lines) + "\n"
