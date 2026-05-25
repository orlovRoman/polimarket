import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import time
import hashlib
from threading import Lock

_news_cache: dict = {}
_news_cache_lock = Lock()
NEWS_CACHE_TTL = 900  # 15 минут


def _get_cached_news(query: str, fetcher_fn, *args) -> list:
    """Универсальный thread-safe TTL-кэш для новостных запросов."""
    key = hashlib.md5(query.lower().encode()).hexdigest()
    now = time.time()

    with _news_cache_lock:
        if key in _news_cache:
            result, ts = _news_cache[key]
            if now - ts < NEWS_CACHE_TTL:
                return result

    result = fetcher_fn(*args)

    with _news_cache_lock:
        _news_cache[key] = (result, now)
        # GC: чистим устаревшие записи
        expired = [k for k, (_, ts) in _news_cache.items() if now - ts > NEWS_CACHE_TTL]
        for k in expired:
            del _news_cache[k]

    return result


def _fetch_rss_news_impl(query: str, limit: int = 5) -> list:
    """Реальная реализация получения RSS. Не вызывать напрямую — использовать fetch_rss_news."""
    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
        news = []
        for item in root.findall(".//item")[:limit]:
            title = item.find("title").text
            news.append(title)
        return news
    except Exception as e:
        print(f"Ошибка при получении RSS: {e}")
        return []


def _fetch_reddit_news_impl(query: str, limit: int = 5) -> list:
    """Реальная реализация получения Reddit. Не вызывать напрямую — использовать fetch_reddit_news."""
    try:
        url = f"https://www.reddit.com/search.json?q={quote(query)}&sort=new&limit={limit}"
        headers = {'User-Agent': 'Mozilla/5.0 PolymarketBot/1.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        posts = []
        for child in data.get('data', {}).get('children', []):
            title = child.get('data', {}).get('title', '')
            if title:
                posts.append(title)
        return posts
    except Exception as e:
        print(f"Ошибка при получении Reddit: {e}")
        return []


def fetch_rss_news(query: str, limit: int = 5) -> list:
    """Получает последние новости через Google News RSS (с кэшированием 15 мин)."""
    return _get_cached_news(query, _fetch_rss_news_impl, query, limit)


def fetch_reddit_news(query: str, limit: int = 5) -> list:
    """Получает последние посты с Reddit по ключевым словам (с кэшированием 15 мин)."""
    return _get_cached_news(query, _fetch_reddit_news_impl, query, limit)
