import sys
import os
import unittest
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import GOOGLE_API_KEY
from agents.shared.utils.gemini_client import generate_content_with_fallback

def evaluate_with_llm(text_to_eval: str, criteria: str) -> bool:
    """Использует LLM-as-a-judge для оценки качества текста."""
    if not GOOGLE_API_KEY:
        return True # Skip if no key
        
    prompt = f"""
    Оцени следующий текст по критерию: "{criteria}".
    Текст: "{text_to_eval}"
    Ответь ТОЛЬКО 'YES' если текст соответствует критерию (хороший), и 'NO' если не соответствует (например, содержит общие слова без конкретики).
    """
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}]
    }
    
    try:
        result, _ = generate_content_with_fallback(GOOGLE_API_KEY, payload)
        if result and 'candidates' in result:
            answer = result['candidates'][0]['content']['parts'][0]['text'].strip().upper()
            return 'YES' in answer
    except Exception:
        pass
    return False

class TestEvalQuality(unittest.TestCase):
    def test_shadow_facts_quality(self):
        # Хороший пример (конкретика)
        good_facts = "Спред 1%, стакан плотный: $50k на bid, $10k на ask. Сильный перекос."
        # Плохой пример (вода)
        bad_facts = "Стакан выглядит нормально, можно торговать."
        
        criteria = "Содержит ли текст конкретные факты из стакана (спред, объемы, перекос bid/ask)?"
        
        # Только если есть ключ (иначе тесты в CI упадут)
        if GOOGLE_API_KEY:
            self.assertTrue(evaluate_with_llm(good_facts, criteria), "Хороший текст должен пройти eval")
            self.assertFalse(evaluate_with_llm(bad_facts, criteria), "Водянистый текст не должен пройти eval")

if __name__ == '__main__':
    unittest.main()
