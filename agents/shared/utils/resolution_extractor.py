"""
Автоматически извлекает источник резолюции из description рынка.
Два уровня: regex (быстро, бесплатно) → LLM fallback (если не нашли).
"""
import re
import httpx
import feedparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse
import asyncio
import logging

logger = logging.getLogger(f"NexusPolyBot.{__name__}")

# ─────────────────────────────────────────────
# Известные домены → их RSS/API endpoint
# ─────────────────────────────────────────────
KNOWN_RSS_MAP: dict[str, str] = {
    # Новости
    "apnews.com":        "https://rsshub.app/apnews/topics/apf-topnews",
    "reuters.com":       "https://feeds.reuters.com/reuters/topNews",
    "bbc.com":           "https://feeds.bbci.co.uk/news/rss.xml",
    "bbc.co.uk":         "https://feeds.bbci.co.uk/news/rss.xml",
    "theguardian.com":   "https://www.theguardian.com/world/rss",
    "nytimes.com":       "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "washingtonpost.com":"https://feeds.washingtonpost.com/rss/world",
    "politico.com":      "https://rss.politico.com/politics-news.xml",
    "thehill.com":       "https://thehill.com/feed/",
    "axios.com":         "https://api.axios.com/feed/",
    "foxnews.com":       "https://moxie.foxnews.com/google-publisher/latest.xml",
    "nbcnews.com":       "https://feeds.nbcnews.com/nbcnews/public/news",
    
    # Спорт
    "espn.com":          "https://www.espn.com/espn/rss/news",
    "nba.com":           "https://www.nba.com/rss/nba_rss.xml",
    "nfl.com":           "https://www.nfl.com/rss/rsslanding.html",
    "mlb.com":           "https://www.mlb.com/feeds/news/rss.xml",
    "sports-reference.com": None,  # только scraping, нет RSS
    
    # Крипто
    "coinmarketcap.com": "https://coinmarketcap.com/rss/",
    "coingecko.com":     None,  # только API
    "coindesk.com":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph.com": "https://cointelegraph.com/rss",
    "decrypt.co":        "https://decrypt.co/feed",
    
    # Официальные организации
    "who.int":           "https://www.who.int/rss-feeds/news-releases.xml",
    "fda.gov":           "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/fda-news-release/rss.xml",
    "federalreserve.gov":"https://www.federalreserve.gov/feeds/press_all.xml",
    "sec.gov":           "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&dateb=&owner=include&count=10&output=atom",
    
    # Метакулюс / предикшн маркеты
    "metaculus.com":     None,  # только API
    "goodjudgment.com":  None,
    
    # Технологии
    "techcrunch.com":    "https://techcrunch.com/feed/",
    "theverge.com":      "https://www.theverge.com/rss/index.xml",
    "wired.com":         "https://www.wired.com/feed/rss",
}

ORACLE_PATTERNS = [
    r"uma\s+oracl",
    r"resolves\s+(via|using|through)\s+uma",
    r"polymarket\s+resolution\s+council",
    r"admin\s+resolution",
]

@dataclass
class ResolutionSource:
    raw_url: Optional[str]          
    domain: Optional[str]           
    rss_url: Optional[str]          
    resolution_type: str            
    extraction_method: str          
    keywords: list[str] = field(default_factory=list)  
    confidence: float = 1.0


# ─────────────────────────────────────────────
# Уровень 1: Regex-извлечение
# ─────────────────────────────────────────────

_URL_IN_MARKDOWN   = re.compile(r'\[.*?\]\((https?://[^\s)]+)\)')
_PLAIN_URL         = re.compile(r'https?://[^\s,\)>"]+')
_RESOLVE_SENTENCE  = re.compile(
    r'(?:resolv|determin|source|according\s+to|based\s+on|per|via)[^.]*?(https?://\S+)',
    re.IGNORECASE
)
_DOMAIN_MENTION    = re.compile(
    r'\b(apnews|reuters|espn|bbc|who\.int|coindesk|cointelegraph|'
    r'politico|axios|thehill|nytimes|washingtonpost|nfl|nba|mlb|'
    r'techcrunch|theverge|wired|coinmarketcap|coingecko|'
    r'fda\.gov|federalreserve|sec\.gov|metaculus)\b',
    re.IGNORECASE
)

def _apex_domain(url: str) -> str:
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parts = urlparse(url).netloc.lower().split(".")
        if len(parts) >= 3 and parts[-2] in ("co", "gov", "org", "com"):
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    except Exception:
        return url

def extract_resolution_source_regex(description: str) -> Optional[ResolutionSource]:
    if not description:
        return None

    for pattern in ORACLE_PATTERNS:
        if re.search(pattern, description, re.IGNORECASE):
            return ResolutionSource(
                raw_url=None, domain=None, rss_url=None,
                resolution_type="oracle",
                extraction_method="regex",
                confidence=0.95
            )

    for pattern in [_RESOLVE_SENTENCE, _URL_IN_MARKDOWN]:
        m = pattern.search(description)
        if m:
            url = m.group(1).rstrip(".,)")
            domain = _apex_domain(url)
            rss = KNOWN_RSS_MAP.get(domain)
            return ResolutionSource(
                raw_url=url,
                domain=domain,
                rss_url=rss,
                resolution_type="rss_monitorable" if rss else "api_only",
                extraction_method="regex",
                confidence=0.9
            )

    m = _PLAIN_URL.search(description)
    if m:
        url = m.group(0).rstrip(".,)")
        domain = _apex_domain(url)
        rss = KNOWN_RSS_MAP.get(domain)
        return ResolutionSource(
            raw_url=url,
            domain=domain,
            rss_url=rss,
            resolution_type="rss_monitorable" if rss else "api_only",
            extraction_method="regex",
            confidence=0.75
        )

    m = _DOMAIN_MENTION.search(description)
    if m:
        hint = m.group(1).lower()
        hint_map = {
            "apnews": "apnews.com", "reuters": "reuters.com",
            "espn": "espn.com", "bbc": "bbc.com",
            "politico": "politico.com", "axios": "axios.com",
            "thehill": "thehill.com", "nytimes": "nytimes.com",
            "coindesk": "coindesk.com", "cointelegraph": "cointelegraph.com",
            "metaculus": "metaculus.com",
        }
        domain = hint_map.get(hint, hint + ".com")
        rss = KNOWN_RSS_MAP.get(domain)
        return ResolutionSource(
            raw_url=None,
            domain=domain,
            rss_url=rss,
            resolution_type="rss_monitorable" if rss else "api_only",
            extraction_method="regex",
            confidence=0.65
        )

    return None

# ─────────────────────────────────────────────
# Уровень 2: LLM fallback (nano-модель)
# ─────────────────────────────────────────────

async def extract_resolution_source_llm(
    description: str,
    api_key: str,
    model: str = "gemini-2.0-flash-lite"
) -> Optional[ResolutionSource]:
    if not description or len(description) < 20:
        return None

    prompt = f"""Extract the resolution source from this prediction market description.
Return ONLY a JSON object, nothing else:
{{
  "source_url": "<full URL or null>",
  "source_domain": "<apex domain like 'reuters.com' or null>",
  "resolution_type": "<one of: news_site | sports_site | crypto_site | government | oracle | social_media | unknown>",
  "confidence": <0.0-1.0>
}}

Description:
{description[:800]}"""

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "maxOutputTokens": 150,
                        "temperature": 0.0,
                    }
                }
            )
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        import json
        parsed = json.loads(text)

        domain = parsed.get("source_domain")
        rss = KNOWN_RSS_MAP.get(domain) if domain else None
        llm_type = parsed.get("resolution_type", "unknown")
        
        if rss:
            res_type = "rss_monitorable"
        elif llm_type == "oracle":
            res_type = "oracle"
        else:
            res_type = "api_only"

        return ResolutionSource(
            raw_url=parsed.get("source_url"),
            domain=domain,
            rss_url=rss,
            resolution_type=res_type,
            extraction_method="llm",
            confidence=float(parsed.get("confidence", 0.5))
        )
    except Exception as e:
        logger.error(f"[resolution_extractor] LLM fallback error: {e}")
        return None

# ─────────────────────────────────────────────
# RSS Discovery (Autodiscover)
# ─────────────────────────────────────────────

import json
import asyncio
import os
from config import VAULT_PATH

_runtime_rss_cache: dict[str, str] = {}
_RSS_CACHE_FILE = VAULT_PATH / "rss_cache.json"
_cache_loaded = False
_rss_lock = asyncio.Lock()

async def _load_rss_cache():
    global _runtime_rss_cache, _cache_loaded
    if _cache_loaded:
        return
    if _RSS_CACHE_FILE.exists():
        try:
            with open(_RSS_CACHE_FILE, "r", encoding="utf-8") as f:
                _runtime_rss_cache = json.load(f)
        except Exception as e:
            logger.error(f"[autodiscover_rss] Ошибка загрузки кэша: {e}")
    _cache_loaded = True

async def _save_rss_cache():
    try:
        tmp_file = _RSS_CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(_runtime_rss_cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, _RSS_CACHE_FILE)
    except Exception as e:
        logger.error(f"[autodiscover_rss] Ошибка сохранения кэша: {e}")

AUTODISCOVER_PATHS = ["/feed", "/rss", "/feed.xml", "/rss.xml", "/feeds/posts/default", "/news/rss"]

async def autodiscover_rss(domain: str) -> Optional[str]:
    async with _rss_lock:
        await _load_rss_cache()
        if domain in _runtime_rss_cache:
            val = _runtime_rss_cache[domain]
            return None if val == "NONE" else val
        
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        for path in AUTODISCOVER_PATHS:
            try:
                url = f"https://{domain}{path}"
                r = await client.get(url, headers={"Accept": "application/rss+xml"})
                if r.status_code == 200 and "xml" in r.headers.get("content-type", ""):
                    async with _rss_lock:
                        _runtime_rss_cache[domain] = url
                        await _save_rss_cache()
                    logger.info(f"[autodiscover_rss] Найдена лента: {domain} -> {url}")
                    return url
            except Exception:
                continue
                
    async with _rss_lock:
        _runtime_rss_cache[domain] = "NONE"
        await _save_rss_cache()
    return None

# ─────────────────────────────────────────────
# Оркестратор
# ─────────────────────────────────────────────

async def get_resolution_source(
    market_description: str,
    market_title: str,
    api_key: str
) -> ResolutionSource:
    result = extract_resolution_source_regex(market_description or "")

    if result is None or result.confidence < 0.6:
        llm_result = await extract_resolution_source_llm(market_description or "", api_key)
        if llm_result and llm_result.confidence > (result.confidence if result else 0):
            result = llm_result

    if result is None:
        result = ResolutionSource(
            raw_url=None, domain=None, rss_url=None,
            resolution_type="unknown",
            extraction_method="fallback",
            confidence=0.0
        )
        
    # Если rss_url не найден, но есть domain и это не api_only/oracle - пытаемся найти
    if result.domain and not result.rss_url and result.resolution_type not in ("oracle", "api_only", "unknown"):
        discovered_rss = await autodiscover_rss(result.domain)
        if discovered_rss:
            result.rss_url = discovered_rss
            result.resolution_type = "rss_monitorable"

    result.keywords = _extract_keywords(market_title)
    return result

def _extract_keywords(title: str) -> list[str]:
    stop_words = {
        "will", "the", "a", "an", "in", "on", "at", "to", "for",
        "of", "and", "or", "by", "be", "is", "are", "this", "that",
        "by", "end", "before", "after", "2024", "2025", "2026",
    }
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9']+", title)
    keywords = [t for t in tokens if t.lower() not in stop_words and len(t) > 3]
    return keywords[:5]

def check_rss_for_keywords(rss_url: str, keywords: list[str]) -> dict:
    try:
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:30]:
            title = entry.get("title", "").lower()
            summary = entry.get("summary", "").lower()
            content = title + " " + summary
            matched = [kw for kw in keywords if kw.lower() in content]
            if len(matched) >= 2:
                return {
                    "found": True,
                    "title": entry.get("title"),
                    "published": entry.get("published"),
                    "link": entry.get("link"),
                    "matched_keywords": matched,
                }
        return {"found": False, "checked_entries": len(feed.entries)}
    except Exception as e:
        return {"found": False, "error": str(e)}

def _build_resolution_block(src: ResolutionSource, hit: dict) -> str:
    if src.resolution_type == "oracle":
        return "[RESOLUTION] Автоматический оракул (UMA/admin) — внешний мониторинг не нужен.\n"
    if src.resolution_type == "unknown":
        return "[RESOLUTION] Источник резолюции не определён — действуй осторожно.\n"

    status = ""
    if hit.get("found"):
        status = (f"✅ СВЕЖАЯ ЗАПИСЬ в {src.domain}:\n"
                  f"  Заголовок: {hit['title']}\n"
                  f"  Опубликовано: {hit.get('published', 'неизвестно')}\n"
                  f"  Совпали ключевые слова: {hit['matched_keywords']}\n"
                  f"  Ссылка: {hit.get('link', '')}\n"
                  f"  ⚠️ ВЫСОКИЙ ПРИОРИТЕТ — событие может резолвиться скоро!")
    elif src.rss_url:
        status = f"❌ Новостей по теме в {src.domain} нет ({hit.get('checked_entries', '?')} записей проверено)"
    else:
        status = f"⚠️ {src.domain} — нет RSS, только scraping (не реализован)"

    return (f"[RESOLUTION SOURCE: {src.domain}]\n"
            f"Метод извлечения: {src.extraction_method} (confidence: {src.confidence:.2f})\n"
            f"URL: {src.raw_url or 'не найден'}\n"
            f"Ключевые слова: {src.keywords}\n"
            f"{status}\n")
