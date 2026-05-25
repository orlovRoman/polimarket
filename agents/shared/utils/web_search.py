import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import time
from functools import wraps

def ttl_cache(maxsize=128, ttl=1800):
    cache = {}
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(kwargs)
            now = time.time()
            if key in cache:
                result, timestamp = cache[key]
                if now - timestamp < ttl:
                    return result
            result = func(*args, **kwargs)
            cache[key] = (result, now)
            if len(cache) > maxsize:
                oldest = min(cache.keys(), key=lambda k: cache[k][1])
                del cache[oldest]
            return result
        return wrapper
    return decorator

@ttl_cache(maxsize=128, ttl=1800)
def fetch_rss_news(query: str, limit: int = 5) -> list[str]:
    """
    Получает последние новости через Google News RSS.
    
    :param query: Поисковый запрос
    :param limit: Количество заголовков
    :return: Список заголовков новостей
    """
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

@ttl_cache(maxsize=128, ttl=1800)
def fetch_reddit_news(query: str, limit: int = 5) -> list[str]:
    """
    Получает последние посты с Reddit по ключевым словам.
    
    :param query: Поисковый запрос
    :param limit: Количество постов
    :return: Список заголовков постов
    """
    try:
        url = f"https://www.reddit.com/search.json?q={quote(query)}&sort=new&limit={limit}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36 PolymarketBot/1.0'}
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
