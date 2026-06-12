"""
Скрапер внешних оракул-ссылок (Sources/Oracle) напрямую из Gamma API Polymarket.
"""
from __future__ import annotations
import re
import logging
from urllib.parse import urlparse
from typing import Optional
import httpx

logger = logging.getLogger("NexusPolyBot.PolymarketSourcesScraper")

# Регулярное выражение для поиска URL
URL_PATTERN = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')

def is_valid_external_url(url: str) -> bool:
    """Проверяет, что ссылка внешняя (не на Polymarket) и валидная."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        netloc = parsed.netloc.lower()
        # Исключаем Polymarket
        if "polymarket.com" in netloc:
            return False
        return True
    except Exception:
        return False

async def fetch_market_oracle_links(market_id: str, market_description: Optional[str] = None) -> list[str]:
    """
    Запрашивает Gamma API для получения внешних оракул-ссылок.
    Возвращает список из максимум 2 внешних уникальных ссылок (первая из resolutionSource,
    остальные из описания).
    """
    links = []
    
    # 1. Попытка запросить Gamma API
    try:
        url = f"https://gamma-api.polymarket.com/markets/{market_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                
                # Добавляем resolutionSource
                res_source = data.get("resolutionSource")
                if res_source and res_source.strip():
                    res_source = res_source.strip()
                    # Если resolutionSource это полноценный URL
                    if res_source.startswith("http://") or res_source.startswith("https://") or res_source.startswith("www."):
                        if not res_source.startswith("http"):
                            res_source = "https://" + res_source
                        if is_valid_external_url(res_source):
                            links.append(res_source)
                            
                # Добавляем ссылки из описания Gamma API
                desc = data.get("description") or ""
                if desc:
                    desc_links = URL_PATTERN.findall(desc)
                    for link in desc_links:
                        link_clean = link.strip()
                        if not link_clean.startswith("http"):
                            link_clean = "https://" + link_clean
                        if is_valid_external_url(link_clean) and link_clean not in links:
                            links.append(link_clean)
                        if len(links) >= 2:
                            break
            else:
                logger.warning(f"[sources_scraper] Gamma API вернул статус {resp.status_code} для рынка {market_id}")
    except Exception as e:
        logger.warning(f"[sources_scraper] Ошибка при запросе Gamma API для рынка {market_id}: {e}")

    # 2. Fallback на переданный локальный description (если Gamma API не вернул ничего или упал)
    if not links and market_description:
        desc_links = URL_PATTERN.findall(market_description)
        for link in desc_links:
            link_clean = link.strip()
            if not link_clean.startswith("http"):
                link_clean = "https://" + link_clean
            if is_valid_external_url(link_clean) and link_clean not in links:
                links.append(link_clean)
            if len(links) >= 2:
                break

    # Очищаем дубликаты с сохранением порядка
    unique_links = []
    for link in links:
        if link not in unique_links:
            unique_links.append(link)

    # Ограничиваем первыми двумя внешними ссылками
    return unique_links[:2]
