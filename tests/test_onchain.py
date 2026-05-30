import unittest
from unittest.mock import patch, MagicMock
from core.smart_money import analyze_smart_money

class TestOnChain(unittest.TestCase):
    
    @patch('core.smart_money.get_known_whales')
    def test_analyze_smart_money(self, mock_get_whales):
        # Настраиваем mock базы данных китов
        mock_get_whales.return_value = {
            "0x123...": {"alias": "Whale 1", "win_rate": 0.8},
            "0xabc...": {"alias": "Whale 2", "win_rate": 0.65}
        }
        
        # Фейковые сделки из CLOB
        trades = [
            {"maker_address": "0x123...", "outcome_index": 0, "size": "1000", "price": "0.5"}, # $500 YES
            {"maker_address": "0xabc...", "outcome_index": 1, "size": "2000", "price": "0.2"}, # $400 NO
            {"taker_address": "0xxyz...", "outcome_index": 0, "size": "500", "price": "0.5"},  # $250 YES
        ]
        
        # Фейковые позиции из Gamma API
        positions = []
        
        result = analyze_smart_money(trades, positions)
        
        self.assertTrue(result.available)
        self.assertEqual(result.total_yes_usd, 750) # 500 + 250
        self.assertEqual(result.total_no_usd, 400)
        
        # Dominance: 750 / (750 + 400) = 750 / 1150 = 0.6521... -> 0.65
        self.assertEqual(result.yes_dominance, 0.65)
        
        # Проверяем топ кошельков
        self.assertIn("Whale 1", result.summary)
        self.assertIn("Whale 2", result.summary)
        self.assertIn("0xxyz...", result.summary) # Неизвестный кит

    def test_empty_data(self):
        result = analyze_smart_money([], [])
        self.assertFalse(result.available)
        self.assertEqual(result.summary, "Ончейн данные недоступны.")

if __name__ == '__main__':
    unittest.main()
