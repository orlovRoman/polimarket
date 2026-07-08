import asyncio
import sys
import os

sys.path.append("/home/orlovrp/polymarket-bot")
os.chdir("/home/orlovrp/polymarket-bot")

from agents.shared.utils.web_search import fetch_rss_news, fetch_reddit_news, fetch_wikipedia_context, fetch_hackernews
from agents.shared.utils.resolution_extractor import scrape_url_text

async def main():
    query = "Will Flavio Cobolli be the 2026 Men's Wimbledon winner?"
    print("Fetching RSS...")
    rss = fetch_rss_news(query)
    print(f"RSS: {len(rss)} items, ~{len(str(rss))} chars")
    
    print("Fetching Reddit...")
    reddit = fetch_reddit_news(query)
    print(f"Reddit: {len(reddit)} items, ~{len(str(reddit))} chars")
    
    print("Fetching Wiki...")
    wiki = fetch_wikipedia_context(query)
    print(f"Wiki: {len(wiki)} items, ~{len(str(wiki))} chars")
    
    print("Fetching HN...")
    hn = fetch_hackernews(query)
    print(f"HN: {len(hn)} items, ~{len(str(hn))} chars")
    
    print("Checking database for price history sizes...")
    import sqlite3
    conn = sqlite3.connect("vault/database.sqlite")
    c = conn.cursor()
    c.execute("SELECT market_id, COUNT(*) FROM market_prices GROUP BY market_id ORDER BY COUNT(*) DESC LIMIT 5")
    rows = c.fetchall()
    print("Top markets by price history size:")
    for row in rows:
        print(f"Market {row[0]}: {row[1]} rows")
        
    c.execute("SELECT id, title, description FROM markets WHERE id='1087925' or id='2683721' LIMIT 2")
    mkts = c.fetchall()
    for m in mkts:
        print(f"Found market {m[0]}: {m[1]}")
        desc_len = len(m[2]) if m[2] else 0
        print(f"Description length: {desc_len} chars")

if __name__ == "__main__":
    asyncio.run(main())
