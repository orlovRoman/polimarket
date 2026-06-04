import requests
import json
import logging
from config import setup_logger, startup_check
from agents.polymarket_arbitrage_agent.src.synthetic.event_loader import load_events_with_levels_from_raw

def main():
    try:
        startup_check()
    except RuntimeError as e:
        print(f"Skipping strict startup check failures for standalone script: {e}")

    logger = setup_logger("run_scan")
    logging.getLogger("agents").setLevel(logging.DEBUG)

    url = "https://gamma-api.polymarket.com/events?limit=50&active=true&closed=false&order=volume&ascending=false"
    logger.info(f"Fetching {url}")
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"Loaded {len(data)} events")
        
        events = load_events_with_levels_from_raw(data)
        logger.info(f"Parsed {len(events)} valid leveled events")
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP Request failed: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
