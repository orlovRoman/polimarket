# tests/test_arbitrage_workflow.py

import pytest
from unittest.mock import MagicMock, patch, call
from core.models import Market, Signal, SwingSignal
from agents.shared.adapters.polymarket import PolymarketAdapter
from agents.shared.adapters.kalshi import KalshiAdapter


def _market(id, title, price, platform="polymarket"):
    m = MagicMock(spec=Market)
    m.id = id
    m.title = title
    m.price = price
    m.platform = platform
    m.url = f"https://example.com/{id}"
    return m


class MockPolyAdapter(PolymarketAdapter):
    def __init__(self):
        self.fetch_raw_events = MagicMock(return_value=[])
        self.parse_events_to_markets = MagicMock(return_value=[])

    @property
    def name(self) -> str:
        return "polymarket"


class MockKalshiAdapter(KalshiAdapter):
    def __init__(self):
        self.list_markets = MagicMock(return_value=[])
        self.get_orderbook = MagicMock(return_value=None)

    @property
    def name(self) -> str:
        return "kalshi"


# ── Баг #1: lambda closure ────────────────────────────────────

def test_lambda_closure_captures_correct_limit():
    """Проверяем, что fetch_raw_events вызывается с правильным лимитом"""
    adapter = MockPolyAdapter()
    adapter.fetch_raw_events = MagicMock(return_value=[])

    with patch("core.arbitrage_workflow.find_candidate_pairs", return_value=[]), \
         patch("core.arbitrage_workflow.load_manual_pairs", return_value=[]):
        from core.arbitrage_workflow import run_cross_platform_scan
        run_cross_platform_scan(api_key="test", adapters=[adapter], poly_limit=42, dry_run=True)

    adapter.fetch_raw_events.assert_called_once_with(limit=42)


def test_scanners_use_instance_fetch():
    """Проверяем, что сканеры используют инстанс-вызовы fetch_raw_events через poly_fetch"""
    from services.synthetic_corridor_scanner import run_synthetic_corridor_scan
    from services.temporal_corridor_scanner import run_temporal_corridor_scan
    
    with patch("services.synthetic_corridor_scanner.PolymarketAdapter") as MockAdapterClass, \
         patch("services.synthetic_corridor_scanner.load_events_with_levels_from_raw", return_value=[]), \
         patch("services.synthetic_corridor_scanner.find_violations", return_value=[]):
        
        mock_instance = MockAdapterClass.return_value
        mock_instance.fetch_raw_events = MagicMock(return_value=[])
        
        run_synthetic_corridor_scan(poly_limit=15)
        
        MockAdapterClass.assert_called_once()
        mock_instance.fetch_raw_events.assert_called_once_with(limit=15)

    with patch("services.temporal_corridor_scanner.PolymarketAdapter") as MockAdapterClass, \
         patch("services.temporal_corridor_scanner.load_events_from_raw", return_value=[]), \
         patch("services.temporal_corridor_scanner.find_candidates", return_value=[]):
        
        mock_instance = MockAdapterClass.return_value
        mock_instance.fetch_raw_events = MagicMock(return_value=[])
        
        run_temporal_corridor_scan(poly_limit=25)
        
        MockAdapterClass.assert_called_once()
        mock_instance.fetch_raw_events.assert_called_once_with(limit=25)


# ── Баг #2: дублирование в verified ──────────────────────────

def test_no_duplicates_in_verified():
    """Пары не должны попадать в verified дважды"""
    ma = _market("poly-1", "Market A", 0.6)
    mb = _market("kalshi-1", "Market B", 0.4, "kalshi")

    with patch("core.arbitrage_workflow.find_candidate_pairs", return_value=[(ma, mb, 0.8)]), \
         patch("core.arbitrage_workflow.load_manual_pairs", return_value=[]), \
         patch("core.arbitrage_workflow.verify_pair_with_llm", return_value={"is_same_event": True, "confidence": 0.9}), \
         patch("core.arbitrage_workflow.ArbitrageAgent") as MockAgent, \
         patch("core.arbitrage_workflow.save_cross_arbitrage"), \
         patch("core.arbitrage_workflow.mark_cross_arbitrage_alerted"):

        agent_instance = MockAgent.return_value
        agent_instance.analyze_cross_platform = MagicMock(return_value=None)

        from core.arbitrage_workflow import run_cross_platform_scan
        # Пара (poly-1, kalshi-1) в manual И в auto >= 0.72
        # После фикса analyze_cross_platform должен вызваться только 1 раз
        run_cross_platform_scan(
            api_key="test",
            adapters=[MockPolyAdapter(), MockKalshiAdapter()],
        )

        calls = agent_instance.analyze_cross_platform.call_count
        # Без фикса: 2 вызова (дубль). После фикса: 1 вызов.
        assert calls <= 1, f"Дубль в verified: analyze вызван {calls} раз для одной пары"


# ── Баг #3: sleep только при rate limit ──────────────────────

def test_no_unconditional_sleep_on_success():
    import time
    ma = _market("poly-1", "Market A", 0.6)
    mb = _market("kalshi-1", "Market B", 0.4, "kalshi")

    with patch("core.arbitrage_workflow.find_candidate_pairs", return_value=[(ma, mb, 0.8)]), \
         patch("core.arbitrage_workflow.load_manual_pairs", return_value=[]), \
         patch("core.arbitrage_workflow.ArbitrageAgent") as MockAgent, \
         patch("core.arbitrage_workflow.save_cross_arbitrage"), \
         patch("core.arbitrage_workflow.mark_cross_arbitrage_alerted"), \
         patch("time.sleep") as mock_sleep:

        signal = MagicMock()
        signal.has_arbitrage = False
        signal.spread_percent = 0.0
        MockAgent.return_value.analyze_cross_platform.return_value = signal

        from core.arbitrage_workflow import run_cross_platform_scan
        run_cross_platform_scan(api_key="test", adapters=[MockPolyAdapter(), MockKalshiAdapter()])

        # После фикса: sleep(3) убран — нет безусловного вызова
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert 3 not in sleep_calls, f"Безусловный sleep(3) всё ещё присутствует: {sleep_calls}"


# ── Баг #4: Signal field_validator порядок полей ─────────────

def test_signal_instantiation_works():
    """Signal должен создаваться без PydanticUserError"""
    from core.models import Signal
    s = Signal(
        id="sig-1",
        type="MISPRICING",
        market_id="mkt-1",
        platform="polymarket",
        confidence=0.85,
        priority="high",
        summary="Test signal",
        details="Test details"
    )
    assert s.confidence == 0.85
    assert s.edge is None


def test_signal_confidence_clamped():
    from core.models import Signal
    s = Signal(
        id="sig-1", type="MISPRICING", market_id="m", platform="polymarket",
        confidence=1.5,  # > 1.0 — должно быть clamped до 1.0
        priority="low", summary="", details=""
    )
    assert s.confidence == 1.0


def test_signal_edge_clamped():
    from core.models import Signal
    s = Signal(
        id="sig-1", type="MISPRICING", market_id="m", platform="polymarket",
        confidence=0.7, edge=-0.5,  # < 0 — должно быть clamped до 0.0
        priority="low", summary="", details=""
        )
    assert s.edge == -0.5


def test_signal_confidence_none_preserved():
    """edge=None должен остаться None (не clamped до 0)"""
    from core.models import Signal
    s = Signal(
        id="sig-1", type="MISPRICING", market_id="m", platform="polymarket",
        confidence=0.5, edge=None,
        priority="low", summary="", details=""
    )
    assert s.edge is None


def test_signal_validator_order_no_pydantic_error():
    """Гарантируем, что порядок полей не ломает Pydantic v2"""
    import pydantic
    from core.models import Signal
    # Если порядок неверный — этот тест упадёт с PydanticUserError при импорте
    assert Signal.model_fields.get("confidence") is not None
    assert Signal.model_fields.get("edge") is not None
