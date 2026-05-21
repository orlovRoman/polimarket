"""
Тестируем, какие параметры сортировки поддерживает Polymarket Gamma API.
Запустить: python scratch/test_polymarket_api.py
Результаты определят, какие стратегии отбора рынков реализуемы напрямую.
"""
import requests

BASE = "https://gamma-api.polymarket.com"

tests = [
    {"order": "volume", "ascending": "false", "desc": "Объём (текущее поведение)"},
    {"order": "startDate", "ascending": "false", "desc": "Дата старта (новые первыми)"},
    {"order": "endDate", "ascending": "true", "desc": "Дата закрытия (ближайшие первыми)"},
    {"order": "liquidity", "ascending": "false", "desc": "Ликвидность"},
    {"order": "created_at", "ascending": "false", "desc": "Дата создания"},
    {"order": "competitive", "ascending": "false", "desc": "Competitive"},
]

print("=" * 60)
print("Polymarket Gamma API — тест параметров сортировки")
print("=" * 60)

for t in tests:
    desc = t.pop("desc")
    params = {**t, "active": "true", "closed": "false", "limit": "3"}
    try:
        r = requests.get(f"{BASE}/markets", params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            count = len(data)
            titles = [d.get("question", "?")[:50] for d in data[:2]]
            print(f"✅ order={t['order']}: HTTP {r.status_code}, {count} results")
            for title in titles:
                print(f"   → {title}")
        else:
            print(f"❌ order={t['order']}: HTTP {r.status_code}")
    except Exception as e:
        print(f"❌ order={t['order']}: {e}")
    print()

# Тест offset (pagination)
print("--- Тест offset/pagination ---")
for offset in [0, 50, 100]:
    params = {"active": "true", "closed": "false", "limit": "3", "offset": str(offset), "order": "volume", "ascending": "false"}
    try:
        r = requests.get(f"{BASE}/markets", params=params, timeout=10)
        data = r.json()
        titles = [d.get("question", "?")[:50] for d in data[:2]]
        print(f"✅ offset={offset}: {len(data)} results")
        for title in titles:
            print(f"   → {title}")
    except Exception as e:
        print(f"❌ offset={offset}: {e}")
    print()

# Тест events API с тегами
print("--- Тест events API с категориями ---")
for tag in ["politics", "crypto", "sports", "science", "business", "culture"]:
    params = {"active": "true", "closed": "false", "limit": "2", "tag_slug": tag}
    try:
        r = requests.get(f"{BASE}/events", params=params, timeout=10)
        data = r.json()
        market_count = sum(len(e.get("markets", [])) for e in data)
        print(f"✅ tag={tag}: {len(data)} events, {market_count} markets")
    except Exception as e:
        print(f"❌ tag={tag}: {e}")
