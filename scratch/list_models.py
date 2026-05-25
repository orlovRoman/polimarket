import os
import requests
from dotenv import load_dotenv

load_dotenv()

grok_key = os.getenv("GROK_API_KEY")
if not grok_key:
    print("No GROK_API_KEY found in environment.")
    exit(1)

headers = {
    "Authorization": f"Bearer {grok_key}",
    "Content-Type": "application/json"
}

# Попробуем сделать запрос к разным моделям и посмотреть код ответа
models_to_test = ["grok-2", "grok-beta", "grok-3", "grok-4", "grok-4.3"]

for model in models_to_test:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    try:
        response = requests.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload)
        print(f"Model: {model} | Status: {response.status_code} | Response: {response.text[:200]}")
    except Exception as e:
        print(f"Model {model} failed:", e)
