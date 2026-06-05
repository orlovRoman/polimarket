# tests/test_favourite_compounder.py
"""Unit-тесты Favourite Compounding — детерминированные, без сети."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from services.favourite_compounder import (
    FavouriteFilter, ROICalculator, ObviousnessValidator,
    run_favourite_scan, FavouriteOpportunity, calibrate_confidence_threshold
)


def _market(price=0.96, volume=15000, hours=24, title="Test market won the race"):
    m = MagicMock()
    m.id = "mkt-test-1"
    m.price = price
    m.volume = volume
    m.title = title
    m.url = "https://polymarket.com/test"
    m.close_time = datetime.now(timezone.utc) + timedelta(hours=hours)
    m._orderbook = None
    return m


# ── FavouriteFilter ──────────────────────────────────────────

class TestFavouriteFilter:
    def test_passes_valid_market(self):
        assert len(FavouriteFilter().scan([_market()])) == 1

    def test_rejects_low_price(self):
        assert len(FavouriteFilter().scan([_market(price=0.94)])) == 0

    def test_rejects_low_volume(self):
        assert len(FavouriteFilter().scan([_market(volume=5000)])) == 0

    def test_rejects_expired_market(self):
        assert len(FavouriteFilter().scan([_market(hours=-1)])) == 0

    def test_rejects_far_future(self):
        assert len(FavouriteFilter().scan([_market(hours=72)])) == 0

    def test_boundary_price_095(self):
        assert len(FavouriteFilter(min_price=0.95).scan([_market(price=0.95)])) == 1

    def test_empty_input(self):
        assert FavouriteFilter().scan([]) == []

    def test_no_close_time_skipped(self):
        m = _market()
        m.close_time = None
        assert len(FavouriteFilter().scan([m])) == 0


# ── ROICalculator ────────────────────────────────────────────

class TestROICalculator:
    def test_roi_positive_at_096(self):
        roi = ROICalculator().compute(0.96, 12.0, 0.005)
        assert roi["roi_net_pct"] > 0

    def test_roi_decreases_with_spread(self):
        r1 = ROICalculator().compute(0.96, 12.0, 0.001)
        r2 = ROICalculator().compute(0.96, 12.0, 0.02)
        assert r1["roi_net_pct"] > r2["roi_net_pct"]

    def test_annualized_roi_higher_for_shorter_windows(self):
        r_short = ROICalculator().compute(0.96, 2.0)
        r_long  = ROICalculator().compute(0.96, 24.0)
        assert r_short["roi_annual_pct"] > r_long["roi_annual_pct"]

    def test_net_price_capped_at_0999(self):
        roi = ROICalculator().compute(0.999, 1.0, 0.1)
        assert roi["net_price"] <= 0.999

    def test_minimum_hours_safety(self):
        # hours_left=0 не должен вызвать ZeroDivisionError
        roi = ROICalculator().compute(0.97, 0.0)
        assert roi["roi_annual_pct"] is not None


# ── ObviousnessValidator ─────────────────────────────────────

class TestObviousnessValidator:
    def test_past_tense_detected(self):
        m = _market(title="Trump won the election")
        conf, reason = ObviousnessValidator().validate(m, 0.96)
        assert conf >= 0.4
        assert "won" in reason or "прошедшее" in reason

    def test_high_price_boosts_confidence(self):
        m = _market(title="Generic question")
        conf_97, _ = ObviousnessValidator().validate(m, 0.97)
        conf_95, _ = ObviousnessValidator().validate(m, 0.95)
        assert conf_97 > conf_95

    def test_confidence_capped_at_1(self):
        m = _market(title="Has already happened and confirmed won elected")
        conf, _ = ObviousnessValidator().validate(m, 0.99)
        assert conf <= 1.0

    def test_no_signals_returns_low_confidence(self):
        m = _market(title="Will it rain tomorrow?", price=0.95)
        m.price = 0.95
        with patch.object(ObviousnessValidator, "_check_google", return_value=(0.0, "")):
            conf, _ = ObviousnessValidator().validate(m, 0.95)
        assert conf < 0.5


# ── run_favourite_scan ───────────────────────────────────────

class TestRunFavouriteScan:
    @patch("agents.shared.python.db.get_compound_settings", return_value={
        "min_price": 0.95, "min_volume": 10000, "max_hours": 48, "virtual_stake": 50
    })
    def test_returns_opportunities(self, mock_cfg):
        markets = [_market(price=0.97, title="Team A won the championship")]
        with patch.object(ObviousnessValidator, "_check_google", return_value=(0.0, "")):
            # Заглушка для calibrate_confidence_threshold, чтобы не ходил в базу
            with patch("services.favourite_compounder.calibrate_confidence_threshold", return_value=0.5):
                opps = run_favourite_scan(markets, min_confidence=0.3)
        assert len(opps) >= 1
        assert isinstance(opps[0], FavouriteOpportunity)

    @patch("agents.shared.python.db.get_compound_settings", return_value={
        "min_price": 0.95, "min_volume": 10000, "max_hours": 48, "virtual_stake": 50
    })
    def test_sorted_by_roi_desc(self, mock_cfg):
        m1 = _market(price=0.97, title="Won the championship")
        m2 = _market(price=0.99, title="Confirmed won elected")
        m2.id = "mkt-2"
        with patch.object(ObviousnessValidator, "_check_google", return_value=(0.0, "")):
            with patch("services.favourite_compounder.calibrate_confidence_threshold", return_value=0.0):
                opps = run_favourite_scan([m1, m2], min_confidence=0.0)
        if len(opps) >= 2:
            assert opps[0].roi_net_pct >= opps[1].roi_net_pct

    @patch("agents.shared.python.db.get_compound_settings", return_value={
        "min_price": 0.95, "min_volume": 10000, "max_hours": 48, "virtual_stake": 50
    })
    def test_low_confidence_filtered_out(self, mock_cfg):
        m = _market(price=0.96, title="Generic ambiguous question")
        with patch.object(ObviousnessValidator, "_check_google", return_value=(0.0, "")):
            with patch("services.favourite_compounder.calibrate_confidence_threshold", return_value=0.9):
                opps = run_favourite_scan([m], min_confidence=0.9)
        assert len(opps) == 0


# ── calibrate_confidence_threshold ────────────────────────────

class TestCalibrateConfidenceThreshold:
    @patch("agents.shared.python.db.get_compound_settings", return_value={"min_confidence": 0.5})
    @patch("agents.shared.python.db.save_compound_setting")
    @patch("agents.shared.python.db.get_connection")
    def test_calibrate_no_data(self, mock_get_conn, mock_save, mock_cfg):
        # Если в базе нет записей для FAVOURITE_COMPOUND, порог не меняется
        mock_get_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        threshold = calibrate_confidence_threshold()
        assert threshold == 0.5
        mock_save.assert_not_called()

    @patch("agents.shared.python.db.get_compound_settings", return_value={"min_confidence": 0.5})
    @patch("agents.shared.python.db.save_compound_setting")
    @patch("agents.shared.python.db.get_connection")
    def test_calibrate_high_win_rate(self, mock_get_conn, mock_save, mock_cfg):
        # Если win_rate > 85%, то порог должен снизиться (т.к. мы слишком уверены)
        mock_get_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "win_rate": 0.90, "total_signals": 10
        }
        threshold = calibrate_confidence_threshold()
        assert threshold == 0.45
        mock_save.assert_called_with("min_confidence", "0.45")

    @patch("agents.shared.python.db.get_compound_settings", return_value={"min_confidence": 0.5})
    @patch("agents.shared.python.db.save_compound_setting")
    @patch("agents.shared.python.db.get_connection")
    def test_calibrate_low_win_rate(self, mock_get_conn, mock_save, mock_cfg):
        # Если win_rate < 70%, то порог должен повыситься (чтобы стать более строгим)
        mock_get_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {
            "win_rate": 0.65, "total_signals": 10
        }
        threshold = calibrate_confidence_threshold()
        assert threshold == 0.55
        mock_save.assert_called_with("min_confidence", "0.55")


# ── Тест бага: ROI spread должен учитывать цену ──────────────
class TestROICalculatorSpreadBug:
    def test_spread_cost_is_relative_to_price(self):
        """spread_pct — относительный, spread_cost должен умножаться на price"""
        calc = ROICalculator()
        roi = calc.compute(price=0.96, hours_left=12.0, spread_pct=0.02)
        # spread_cost = 0.96 * 0.02 / 2 = 0.0096
        # net_price = 0.96 + 0.0096 = 0.9696
        assert abs(roi["net_price"] - 0.9696) < 0.001, \
            f"net_price={roi['net_price']}, ожидалось ~0.9696"

    def test_spread_cost_zero_doesnt_change_price(self):
        roi = ROICalculator().compute(0.96, 12.0, spread_pct=0.0)
        assert roi["net_price"] == 0.96


# ── Тест бага: calibrate при NULL win_rate ────────────────────
class TestCalibrateNullWinRate:
    @patch("agents.shared.python.db.get_compound_settings",
           return_value={"min_confidence": 0.5})
    @patch("agents.shared.python.db.save_compound_setting")
    @patch("agents.shared.python.db.get_connection")
    def test_null_win_rate_returns_current_threshold(self, mock_conn, mock_save, mock_cfg):
        """NULL win_rate в БД не должен вызывать TypeError"""
        mock_conn.return_value.__enter__.return_value \
            .execute.return_value.fetchone.return_value = {
                "win_rate": None, "total_signals": 10
            }
        threshold = calibrate_confidence_threshold()
        assert threshold == 0.5
        mock_save.assert_not_called()


# ── Тест: ImportError в _check_google ────────────────────────
class TestObviousnessGoogleImportError:
    def test_check_google_import_error_returns_zero(self):
        """Если core.workflow не импортируется — graceful fallback"""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "core.workflow":
                raise ImportError("модуль недоступен")
            return real_import(name, *args, **kwargs)

        m = _market(title="Generic question", price=0.95)
        with patch("builtins.__import__", side_effect=mock_import):
            conf, reason = ObviousnessValidator()._check_google(m.title)
        assert conf == 0.0
        assert reason == ""


# ── Тест: volume не MagicMock ─────────────────────────────────
class TestFavouriteFilterVolumeSafety:
    def test_mock_volume_attribute_doesnt_crash(self):
        """Если volume — MagicMock, scan должен продолжить, а не упасть"""
        from unittest.mock import MagicMock
        m = _market()
        m.volume = MagicMock()   # не число
        # Не должен выбросить исключение
        result = FavouriteFilter().scan([m])
        # volume не является числом, должен быть заменён на 0
        assert isinstance(result, list)
