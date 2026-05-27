import os
from dotenv import load_dotenv
import requests

load_dotenv()
or_key = os.getenv("OPENROUTER_API_KEY")

headers = {
    "Authorization": f"Bearer {or_key}",
    "Content-Type": "application/json"
}
payload = {
    "model": "meta-llama/llama-3.3-70b-instruct",  # Это ПЛАТНАЯ модель
    "messages": [{"role": "user", "content": "Reply with OK."}]
}
print("Testing paid OpenRouter model...")
response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
print(f"Status: {response.status_code}")
print(f"Response: {response.text}")
