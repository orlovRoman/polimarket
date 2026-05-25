import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Добавляем корень проекта для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from agents.shared.utils.gemini_client import generate_content_with_fallback

payload = {
    "contents": [
        {
            "role": "user",
            "parts": [
                {"text": "Hello, write a very short 1-sentence welcome message for a trading bot."}
            ]
        }
    ]
}

print("Testing OpenRouter integration locally...")
result, model_used = generate_content_with_fallback(
    api_key=os.getenv("GOOGLE_API_KEY", ""),
    payload=payload,
    default_model="gemini-2.5-flash",
    agent_name="TEST-BOT",
    timeout=30
)

print("\n--- RESULTS ---")
print("Model used:", model_used)
if result:
    try:
        text = result['candidates'][0]['content']['parts'][0]['text']
        print("Response:", text)
    except Exception as e:
        print("Failed to parse result:", e)
        print("Raw result:", result)
else:
    print("Failed to get any response.")
