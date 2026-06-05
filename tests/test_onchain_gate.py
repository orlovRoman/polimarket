import os
import unittest
from unittest.mock import patch, MagicMock
from core.onchain_gate import check_onchain_gate, GateResult, get_cluster_size_for_market
from core.onchain_scorer import OnchainScore

class TestOnchainGate(unittest.TestCase):

    def test_pytest_bypass_by_default(self):
        # По умолчанию при запуске тестов (когда установлен PYTEST_CURRENT_TEST)
        # гейт должен возвращать allow=True.
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "test_some_dummy_test"}):
            res = check_onchain_gate(
                oc_score=None,
                market_id="test_market",
                total_volume_usd=100.0,
                market_tag="default",
                ignore_pytest=False
            )
            self.assertTrue(res.allow)
            self.assertEqual(res.blocked_by, "pass")
            self.assertEqual(res.reason, "pytest bypass")

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_volume_limit(self, mock_config, mock_get_cluster):
        # Проверяем фильтрацию по объему торгов.
        # Задаем лимиты: min_volume = 5000, min_whales = 1
        mock_config.get_swing_min_volume_sync.return_value = 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 0

        # Объем меньше порога (4900 < 5000)
        oc_score = OnchainScore(score=1.0, confidence=0.8, direction="CONFIRM", annotation="Whale confirmation", whale_count=2, yes_dominance=0.8)
        
        # Передаем ignore_pytest=True, чтобы не сработал обход pytest
        res = check_onchain_gate(
            oc_score=oc_score,
            market_id="market_1",
            total_volume_usd=4900.0,
            market_tag="default",
            ignore_pytest=True
        )
        self.assertFalse(res.allow)
        self.assertEqual(res.blocked_by, "volume")
        self.assertIn("Объём $4,900 < порог $5,000", res.reason)

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_no_smart_money(self, mock_config, mock_get_cluster):
        # Проверяем фильтрацию, когда объем ок, но нет китов или кластеров.
        mock_config.get_swing_min_volume_sync.return_value = 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 0

        # 0 китов, 0 кластеров
        oc_score = OnchainScore(score=0.0, confidence=0.0, direction="NEUTRAL", annotation="No data", whale_count=0, yes_dominance=0.0)
        
        res = check_onchain_gate(
            oc_score=oc_score,
            market_id="market_1",
            total_volume_usd=6000.0,
            market_tag="default",
            ignore_pytest=True
        )
        self.assertFalse(res.allow)
        self.assertEqual(res.blocked_by, "whales")
        self.assertIn("Нет умных денег", res.reason)

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_has_whale(self, mock_config, mock_get_cluster):
        # Проверяем, что проходит гейт при наличии китов.
        mock_config.get_swing_min_volume_sync.return_value = 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 0

        # 1 кит, 0 кластеров
        oc_score = OnchainScore(score=1.0, confidence=0.8, direction="CONFIRM", annotation="Whale confirmation", whale_count=1, yes_dominance=0.8)
        
        res = check_onchain_gate(
            oc_score=oc_score,
            market_id="market_1",
            total_volume_usd=6000.0,
            market_tag="default",
            ignore_pytest=True
        )
        self.assertTrue(res.allow)
        self.assertEqual(res.blocked_by, "pass")
        self.assertIn("whales=1", res.reason)

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_has_cluster(self, mock_config, mock_get_cluster):
        # Проверяем, что проходит гейт при наличии кластеров (даже без китов).
        mock_config.get_swing_min_volume_sync.return_value = 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 1 # Есть кластер!

        # 0 китов
        oc_score = OnchainScore(score=0.0, confidence=0.0, direction="NEUTRAL", annotation="No data", whale_count=0, yes_dominance=0.0)
        
        res = check_onchain_gate(
            oc_score=oc_score,
            market_id="market_1",
            total_volume_usd=6000.0,
            market_tag="default",
            ignore_pytest=True
        )
        self.assertTrue(res.allow)
        self.assertEqual(res.blocked_by, "pass")
        self.assertIn("clusters=1", res.reason)

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_none_score_safety(self, mock_config, mock_get_cluster):
        # Проверяем безопасность при oc_score=None
        mock_config.get_swing_min_volume_sync.return_value = 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 0

        res = check_onchain_gate(
            oc_score=None,
            market_id="market_1",
            total_volume_usd=6000.0,
            market_tag="default",
            ignore_pytest=True
        )
        self.assertFalse(res.allow)
        self.assertEqual(res.blocked_by, "whales")
        self.assertIn("known_whales=0", res.reason)

    @patch("core.onchain_gate.get_cluster_size_for_market")
    @patch("core.onchain_gate.ConfigProvider")
    def test_gate_sports_tag_volume(self, mock_config, mock_get_cluster):
        # Проверяем фильтрацию по tag-специфичному объему
        # Для default - 5000, для sports - 2000
        mock_config.get_swing_min_volume_sync.side_effect = lambda tag: 2000.0 if tag == "sports" else 5000.0
        mock_config.get_swing_min_whale_count_sync.return_value = 1
        mock_get_cluster.return_value = 1

        # Объем 3000 для дефолта заблокировался бы, но для sports должен пройти
        res = check_onchain_gate(
            oc_score=None,
            market_id="market_1",
            total_volume_usd=3000.0,
            market_tag="sports",
            ignore_pytest=True
        )
        self.assertTrue(res.allow)
        self.assertEqual(res.blocked_by, "pass")

if __name__ == '__main__':
    unittest.main()
