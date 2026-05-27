import os
from dotenv import load_dotenv
import requests

load_dotenv()

or_key = os.getenv("OPENROUTER_API_KEY")
print(f"OpenRouter key starts with: {or_key[:10]}..." if or_key else "No OpenRouter key")

if or_key:
    headers = {
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/orlovRoman/polimarket",
        "X-Title": "Polymarket Bot Team"
    }
    
    payload = {
        "model": "google/gemini-2.5-flash:free",
        "messages": [{"role": "user", "content": "Hello! Reply with OK."}]
    }
    
    print("\nTesting OpenRouter...")
    response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    
grok_key = os.getenv("GROK_API_KEY")
print(f"\nGrok key starts with: {grok_key[:10]}..." if grok_key else "\nNo Grok key")

if grok_key:
    headers = {
        "Authorization": f"Bearer {grok_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "grok-3",
        "messages": [{"role": "user", "content": "Hello! Reply with OK."}]
    }
    print("\nTesting Grok...")
    response = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
