def deduplicate_headlines(rss_titles: list[str], grounded_text: str) -> list[str]:
    """
    Убирает RSS заголовки, которые уже присутствуют в grounded_text (результатах Google Search).
    """
    if not rss_titles or not grounded_text:
        return rss_titles or []
    
    grounded_lower = grounded_text.lower()
    deduped = []
    for title in rss_titles:
        # Убираем теги даты вида [2026-05-31 12:00]
        clean_title = title
        if title.startswith("[") and "]" in title:
            parts = title.split("]", 1)
            if len(parts) > 1:
                clean_title = parts[1].strip()
        
        # Выделяем слова длиной более 2 символов
        words = [w for w in clean_title.lower().split() if len(w) > 2]
        if not words:
            deduped.append(title)
            continue
            
        # Проверяем первые 4 значимых слова
        sample = words[:4]
        if len(sample) >= 2:
            # Если все проверяемые слова есть в тексте Google Search, то это дубликат
            if all(word in grounded_lower for word in sample):
                continue
        deduped.append(title)
        
    return deduped
