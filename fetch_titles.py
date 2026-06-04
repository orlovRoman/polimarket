import requests
import json

try:
    resp = requests.get("https://gamma-api.polymarket.com/events?limit=20&order=volume24hr&active=true", timeout=10)
    resp.raise_for_status()
    data = resp.json()
    for e in data:
        print(e.get("title", ""))
except Exception as e:
    print(f"Error: {e}")
