import requests
import json

try:
    url = "https://gamma-api.polymarket.com/markets?limit=10&active=true&closed=false&order=volume&ascending=false"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    for e in data:
        print(f"[{e.get('id')}] {e.get('question', '')}")
except Exception as e:
    print(f"Error: {e}")
