import requests
import json
from config import logger
import logging
logging.basicConfig(level=logging.DEBUG)
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw

def main():
    url = "https://gamma-api.polymarket.com/events?limit=50&active=true&closed=false&order=volume&ascending=false"
    print(f"Fetching {url}")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    print(f"Loaded {len(data)} events")
    
    events = load_events_with_levels_from_raw(data)
    print(f"Parsed {len(events)} valid leveled events")

if __name__ == "__main__":
    main()
