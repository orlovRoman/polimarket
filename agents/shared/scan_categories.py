"""
Справочник категорий для /scan.
Ключ = callback_data после 'scan_'
"""

SCAN_CATEGORIES: dict[str, dict] = {
    "politics":      {"label": "🏛 Политика",       "tags": ["politics", "elections", "government"]},
    "crypto":        {"label": "₿ Крипто",           "tags": ["crypto", "bitcoin", "ethereum"]},
    "sports":        {"label": "⚽ Спорт",            "tags": ["sports", "football", "basketball", "soccer"]},
    "science":       {"label": "🔬 Наука/Тех",        "tags": ["science", "technology", "ai", "space"]},
    "culture":       {"label": "🎬 Культура",         "tags": ["culture", "entertainment", "awards"]},
    "business":      {"label": "💼 Бизнес",           "tags": ["business", "economy", "stocks"]},
    "weather":       {"label": "🌦 Погода/Климат",    "tags": ["weather", "climate", "environment"]},
    "entertainment": {"label": "🎮 Игры/Кино",        "tags": ["gaming", "movies", "tv", "esports"]},
    "geopolitics":   {"label": "🌍 Геополитика",      "tags": ["geopolitics", "war", "international", "nato"]},
    "health":        {"label": "🏥 Здоровье",         "tags": ["health", "medicine", "pandemic"]},
    "penny_stocks":  {"label": "🪙 Penny Stocks",     "tags": []},
}
