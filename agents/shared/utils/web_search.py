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
            title = item.find("title").text or ""
            pub_date = item.find("pubDate")
            date_str = pub_date.text[:16] if pub_date is not None and pub_date.text else "дата неизвестна"
            news.append(f"[{date_str}] {title}")
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
            d = child.get('data', {})
            title = d.get('title', '')
            score = d.get('score', 0)
            sub = d.get('subreddit', '')
            if title:
                posts.append(f"[r/{sub}, ↑{score}] {title}")
        return posts
    except Exception as e:
        print(f"Ошибка при получении Reddit: {e}")
        return []


def fetch_rss_news(query: str, limit: int = 5) -> list:
    """Получает последние новости через Google News RSS (с кэшированием 15 мин)."""
    return _get_cached_news(query, _fetch_rss_news_impl, query, limit)


def fetch_reddit_news(query: str, limit: int = 5) -> list:
    """Получает последние посты с Reddit по ключевым словам (с кэшированием 15 мин)."""
    return _get_cached_news(f"reddit:{query}", _fetch_reddit_news_impl, query, limit)

def _fetch_wikipedia_context_impl(query: str, limit: int = 3) -> list:
    """Реальная реализация поиска Wikipedia."""
    try:
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote(query)}&utf8=&format=json&srlimit={limit}"
        response = requests.get(search_url, timeout=10)
        if response.status_code != 200:
            return []
        data = response.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            snippet = item.get("snippet", "").replace('<span class="searchmatch">', '').replace('</span>', '')
            import re
            snippet = re.sub(r'<[^>]+>', '', snippet)
            results.append(f"{item.get('title')}: {snippet}")
        return results
    except Exception as e:
        print(f"Ошибка при получении Wikipedia: {e}")
        return []

def fetch_wikipedia_context(query: str, limit: int = 3) -> list:
    """Ищет факты в Wikipedia — полезно для спорта, политики, турниров."""
    return _get_cached_news(f"wiki:{query}", _fetch_wikipedia_context_impl, query, limit)

def _fetch_google_trends_impl(query: str) -> str:
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl='en-US', tz=0, timeout=(5, 15))
        kw = [query[:100]]
        pt.build_payload(kw, timeframe='now 7-d')
        df = pt.interest_over_time()
        if df.empty:
            return "Google Trends: нет данных"
        latest = int(df[kw[0]].iloc[-1])
        peak = int(df[kw[0]].max())
        last3 = df[kw[0]].iloc[-3:].tolist()
        trend = "📈 растёт" if last3[-1] > last3[0] else ("📉 падает" if last3[-1] < last3[0] else "➡️ стабильно")
        return f"Google Trends (7д): интерес сейчас {latest}/100, пик {peak}/100, тренд {trend}"
    except Exception as e:
        return f"Google Trends: недоступно ({e})"

def fetch_google_trends(query: str) -> str:
    """Возвращает уровень интереса к теме за 7 дней по Google Trends."""
    return _get_cached_news(f"trends:{query}", _fetch_google_trends_impl, query)

def _fetch_hackernews_impl(query: str, limit: int = 3) -> list:
    try:
        from urllib.parse import quote
        url = f"https://hn.algolia.com/api/v1/search?query={quote(query)}&tags=story&hitsPerPage={limit}"
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        hits = r.json().get("hits", [])
        return [f"[HN, ↑{h.get('points', 0)}] {h.get('title', '')}" for h in hits if h.get('title')]
    except Exception as e:
        return []

def fetch_hackernews(query: str, limit: int = 3) -> list:
    """Получает топ-посты с HackerNews по теме (бесплатно, без ключа)."""
    return _get_cached_news(f"hn:{query}", _fetch_hackernews_impl, query, limit)

def build_search_query(market_title: str) -> str:
    """Строит короткий поисковый запрос из заголовка рынка."""
    import re
    # Убираем вопросительные слова и стоп-слова
    stopwords = ["will", "who", "what", "when", "is", "are", "does", "can",
                 "the", "a", "an", "in", "of", "to", "by", "for",
                 "будет", "ли", "что", "кто", "когда", "выиграет"]
    words = re.sub(r'[^\w\s]', '', market_title.lower()).split()
    keywords = [w for w in words if w not in stopwords and len(w) > 2]
    return " ".join(keywords[:6])
