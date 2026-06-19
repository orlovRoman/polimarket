import pytest
import sqlite3
import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from core.models import Market, Signal, AgentOpinion
from core.context import MarketContext, SmartMoneySummary
from agents.shared.python.db import init_db, get_connection, save_market, get_known_whales, save_trader_transaction
from agents.shared.python.resolution import resolve_closed_markets
from services.wallet_tracker import ingest_trades, recalculate_win_rates
from core.onchain_scorer import compute_onchain_score
from core.whale_gate import check_whale_gate
from services.onchain_trend_alert import scan_volume_spikes, build_spike_message
from services.onchain_provider import _cached_get, get_recent_trades, get_top_positions
from agents.polymarket_insider_agent.src.agent import ShadowAgent


@pytest.fixture(autouse=True)
def setup_test_db():
    """Инициализирует тестовую базу данных в памяти."""
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM trader_transactions")
        conn.execute("DELETE FROM wallets")
        conn.execute("DELETE FROM markets")
        conn.execute("DELETE FROM signals")
        conn.execute("DELETE FROM sent_alerts")
    yield


def test_wallet_tracker_ingestion():
    # 1. Записываем тестовый рынок в БД
    market = Market(
        id="test_market_1",
        platform="polymarket",
        title="Will Bitcoin reach 100k?",
        url="https://polymarket.com/market/btc-100k",
        outcome="YES",
        price=0.65,
        close_time=datetime.now(timezone.utc) + timedelta(days=5),
        condition_id="cond_btc_100k"
    )
    save_market(market)

    # 2. Мокаем сделки: одна крупная ($500+), одна мелкая (<$500)
    trades = [
        {"maker_address": "0xWhaleA", "size": "1000", "price": "0.7", "outcome_index": 0},  # $700 -> YES
        {"maker_address": "0xShrimp", "size": "100", "price": "0.5", "outcome_index": 1},   # $50 -> NO (игнорируется)
        {"taker_address": "0xWhaleB", "size": "2000", "price": "0.6", "outcome_index": 1}   # $1200 -> NO
    ]

    saved = ingest_trades("test_market_1", trades)
    assert saved == 2

    # Проверяем, что в БД записались правильные данные
    with get_connection() as conn:
        txs = conn.execute("SELECT * FROM trader_transactions ORDER BY amount_usd ASC").fetchall()
        assert len(txs) == 2
        assert txs[0]["wallet_address"] == "0xWhaleA"
        assert txs[0]["outcome"] == "YES"
        assert txs[0]["amount_usd"] == pytest.approx(700.0)

        assert txs[1]["wallet_address"] == "0xWhaleB"
        assert txs[1]["outcome"] == "NO"
        assert txs[1]["amount_usd"] == pytest.approx(1200.0)


def test_wallet_win_rate_recalculation():
    # Создаем закрытый рынок в БД
    market = Market(
        id="test_market_wr",
        platform="polymarket",
        title="Will ETH merge?",
        url="https://polymarket.com/market/eth-merge",
        outcome="YES",  # исход YES
        price=0.9,
        close_time=datetime.now(timezone.utc) - timedelta(days=1),
        condition_id="cond_eth_merge"
    )
    save_market(market)

    # Используем save_trader_transaction, чтобы кошелек автоматически добавился в wallets
    save_trader_transaction('0xWhaleWR', 'test_market_wr', 'YES', 1000.0, 0.9)
    save_trader_transaction('0xWhaleWR', 'test_market_wr', 'YES', 500.0, 0.9)
    save_trader_transaction('0xWhaleWR', 'test_market_wr', 'NO', 800.0, 0.1)

    # Считаем win_rate
    updated = recalculate_win_rates()
    assert updated == 1

    # Проверяем win_rate в wallets
    with get_connection() as conn:
        wallet = conn.execute("SELECT * FROM wallets WHERE address = '0xWhaleWR'").fetchone()
        assert wallet is not None
        assert wallet["win_rate"] == pytest.approx(0.667, abs=0.001)  # 2 победы из 3 сделок
        assert wallet["total_profit"] == pytest.approx(2300.0)


def test_onchain_scorer():
    # 1. Мало объемов
    sm_low = SmartMoneySummary(
        available=True,
        total_yes_usd=50.0,
        total_no_usd=50.0,
        yes_dominance=0.5,
        top_wallets=[],
        summary=""
    )
    score_low = compute_onchain_score(sm_low, "YES")
    assert score_low.score == pytest.approx(0.0)
    assert score_low.direction == "NEUTRAL"

    # 2. Высокое доминирование YES, нет известных китов
    sm_high = SmartMoneySummary(
        available=True,
        total_yes_usd=9000.0,
        total_no_usd=1000.0,
        yes_dominance=0.9,
        top_wallets=["  0xAddr123... | WR: 80% → YES $9000"],
        summary=""
    )
    
    with patch("core.onchain_scorer.get_known_whales") as mock_whales:
        # Допустим, кит в БД имеет win_rate = 75% и является подтвержденным инсайдером
        mock_whales.return_value = {
            "0xAddr123abc": {"alias": "0xAddr123...", "win_rate": 0.75, "total_profit": 50000.0, "is_insider": True}
        }
        score = compute_onchain_score(sm_high, "YES")
        
        # dom=0.9 -> raw_score = (0.9 - 0.5)*2 = 0.8
        # whale_boost: 0.15 (wr > 0.6)
        # final = 0.8 + 0.15 = 0.95
        assert score.score == pytest.approx(0.95, abs=0.01)
        assert score.direction == "CONFIRM"
        assert score.whale_count == 1
        assert "SmartMoney: YES dom=90%" in score.annotation


def test_whale_gate():
    # 1. Низкое доверие
    oc_low = MagicMock(confidence=0.2, direction="CONTRA", whale_count=3, score=-0.8)
    gate_low = check_whale_gate(oc_low)
    assert gate_low.allow is True

    # 2. Whales торгуют CONTRA, высокий confidence
    oc_contra = MagicMock(confidence=0.7, direction="CONTRA", whale_count=2, score=-0.7)
    gate_contra = check_whale_gate(oc_contra)
    assert gate_contra.allow is False
    assert "Whale Gate" in gate_contra.reason

    # 3. Whales подтверждают CONFIRM
    oc_confirm = MagicMock(confidence=0.7, direction="CONFIRM", whale_count=2, score=0.8)
    gate_confirm = check_whale_gate(oc_confirm)
    assert gate_confirm.allow is True


def test_onchain_trend_alert():
    # Создаем рынок
    market = Market(
        id="trend_m",
        platform="polymarket",
        title="Will AI build a startup?",
        url="https://polymarket.com/market/ai-startup",
        outcome="YES",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(days=2),
        condition_id="cond_ai_startup"
    )
    save_market(market)

    # Насыпаем транзакции с использованием strftime('%Y-%m-%d %H:%M:%S') в UTC
    now = datetime.now(timezone.utc)
    t_prev = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    t_recent = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO trader_transactions (wallet_address, market_id, outcome, amount_usd, price, timestamp)
            VALUES 
            ('0x1', 'trend_m', 'YES', 100.0, 0.5, ?),
            ('0x2', 'trend_m', 'NO', 100.0, 0.5, ?),
            ('0x3', 'trend_m', 'YES', 500.0, 0.5, ?),
            ('0x4', 'trend_m', 'YES', 300.0, 0.5, ?)
        """, (t_prev, t_prev, t_recent, t_recent))

    spikes = scan_volume_spikes(min_spike_ratio=3.0)
    assert len(spikes) == 1
    assert spikes[0]["market_id"] == "trend_m"
    assert spikes[0]["vol_recent"] == pytest.approx(800.0)
    assert spikes[0]["vol_prev"] == pytest.approx(200.0)

    msg = build_spike_message(spikes[0])
    assert "Ончейн-всплеск объёма" in msg
    assert "x4.0" in msg
    assert "YES: $800" in msg


def test_onchain_provider_caching_and_thread_safety():
    # Тестируем thread-safety кэша и httpx-запросы в onchain_provider
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"maker_address": "0xABC"}]}
        mock_get.return_value = mock_resp

        # Проверим, что первый запрос вызывает API, а второй идет из кэша
        data1 = get_recent_trades("cond_test_cache")
        data2 = get_recent_trades("cond_test_cache")

        assert len(data1) == 1
        assert len(data2) == 1
        assert mock_get.call_count == 1  # Только один вызов, второй из кэша!

        # Многопоточный вызов
        threads = []
        for i in range(5):
            t = threading.Thread(target=get_recent_trades, args=("cond_test_cache_multi",))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()


def test_shadow_agent_prompt_token_optimization():
    # Проверяем, что промпт ShadowAgent использует сжатую onchain_annotation,
    # если она задана в MarketContext.
    market = Market(
        id="test_opt",
        platform="polymarket",
        title="Will it snow?",
        url="https://polymarket.com/snow",
        outcome="YES",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(days=1),
        condition_id="cond_snow"
    )
    
    # 1. Сценарий с onchain_annotation
    ctx_with_annotation = MarketContext(
        market=market,
        onchain_annotation="SmartMoney: YES dom=90%, vol=$50k, 2 whale(s) -> score=+0.90"
    )
    
    # 2. Сценарий без onchain_annotation, но с полным SmartMoneySummary
    sm_full = SmartMoneySummary(
        available=True,
        total_yes_usd=45000.0,
        total_no_usd=5000.0,
        yes_dominance=0.9,
        top_wallets=[
            "  0xWhaleA | WR: 75% -> YES $25,000",
            "  0xWhaleB | WR: 80% -> YES $20,000"
        ],
        summary="Top wallets bet YES"
    )
    ctx_without_annotation = MarketContext(
        market=market,
        smart_money=sm_full
    )
    
    # Используем object.__setattr__ для установки transactions
    object.__setattr__(ctx_without_annotation.smart_money, 'transactions', [
        {"alias": "0xWhaleA", "outcome": "YES", "amount_usd": 25000.0, "win_rate": 75.0},
        {"alias": "0xWhaleB", "outcome": "YES", "amount_usd": 20000.0, "win_rate": 80.0}
    ])
    
    agent = ShadowAgent(api_key="test_key")
    
    # Замокаем generate_content_with_fallback
    with patch("agents.shared.utils.gemini_client.generate_content_with_fallback") as mock_gen:
        mock_gen.return_value = (None, "gemini")
        
        # Вызываем для контекста С аннотацией
        agent.analyze_idea(ctx_with_annotation, "SCOUT opinion")
        prompt_with = mock_gen.call_args[1]["payload"]["contents"][0]["parts"][0]["text"]
        
        # Вызываем для контекста БЕЗ аннотации
        agent.analyze_idea(ctx_without_annotation, "SCOUT opinion")
        prompt_without = mock_gen.call_args[1]["payload"]["contents"][0]["parts"][0]["text"]
        
        # Проверяем, что в первом случае промпт содержит только аннотацию
        assert "📊 SmartMoney: YES dom=90%" in prompt_with
        assert "Top wallets bet YES" not in prompt_with
        
        # А во втором содержит полную информацию
        assert "=== ОНЧЕЙН АКТИВНОСТЬ (Smart Money) ===" in prompt_without
        assert "0xWhaleA" in prompt_without


def test_new_whale_scanners_and_early_resolution():
    from services.onchain_trend_alert import scan_large_single_bets, scan_wallet_series
    from services.outcome_tracker import run_resolution_cycle
    from agents.shared.python.db import save_market, get_connection
    from core.models import Market
    import uuid

    # 1. Создаем два рынка в БД
    market_id_1 = f"mkt_whale_test_1_{uuid.uuid4().hex[:8]}"
    market_1 = Market(
        id=market_id_1,
        platform="polymarket",
        title="Will Whale Test 1 succeed?",
        url="https://polymarket.com/whale-test-1",
        outcome="",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(days=10),
        condition_id=f"cond_{market_id_1}",
        volume=10000.0
    )
    save_market(market_1)

    market_id_2 = f"mkt_whale_test_2_{uuid.uuid4().hex[:8]}"
    market_2 = Market(
        id=market_id_2,
        platform="polymarket",
        title="Will Whale Test 2 succeed?",
        url="https://polymarket.com/whale-test-2",
        outcome="",
        price=0.5,
        close_time=datetime.now(timezone.utc) + timedelta(days=10),
        condition_id=f"cond_{market_id_2}",
        volume=10000.0
    )
    save_market(market_2)

    # 2. Добавляем крупную одиночную транзакцию (> $1000) для рынка 1
    now = datetime.now(timezone.utc)
    t_recent = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO wallets (address, win_rate, n_trades, is_insider)
            VALUES ('0xWhaleSingle', 0.8, 50, 1)
        """)
        conn.execute("""
            INSERT INTO trader_transactions (wallet_address, market_id, outcome, amount_usd, price, timestamp)
            VALUES ('0xWhaleSingle', ?, 'YES', 1500.0, 0.5, ?)
        """, (market_id_1, t_recent))

    # Запускаем скан крупных одиночных ставок
    large_bets = scan_large_single_bets()
    assert len(large_bets) == 1
    assert large_bets[0]["market_id"] == market_id_1
    assert large_bets[0]["wallet_address"] == "0xWhaleSingle"
    assert large_bets[0]["amount_usd"] == pytest.approx(1500.0)

    # Проверяем, что сигнал записан в signals
    with get_connection() as conn:
        sig = conn.execute("SELECT * FROM signals WHERE market_id = ? AND strategy_type = 'whale' AND summary LIKE '%single%'", (market_id_1,)).fetchone()
        assert sig is not None
        assert sig["status"] == "PENDING"
        assert sig["target_outcome"] == "YES"

    # 3. Добавляем серию транзакций от другого кошелька
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO wallets (address, win_rate, n_trades, is_insider)
            VALUES ('0xWhaleSeries', 0.8, 50, 1)
        """)
        # 5 транзакций по $500 на NO
        for i in range(5):
            t_tx = (now - timedelta(minutes=5-i)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
                INSERT INTO trader_transactions (wallet_address, market_id, outcome, amount_usd, price, timestamp)
                VALUES ('0xWhaleSeries', ?, 'NO', 500.0, 0.5, ?)
            """, (market_id_2, t_tx))

    # Запускаем скан серий сделок
    series_bets = scan_wallet_series()
    assert len(series_bets) == 1
    assert series_bets[0]["market_id"] == market_id_2
    assert series_bets[0]["wallet_address"] == "0xWhaleSeries"
    assert series_bets[0]["total_amount_usd"] == pytest.approx(2500.0)

    # Проверяем, что сигнал серии записан в signals
    with get_connection() as conn:
        sig_series = conn.execute("SELECT * FROM signals WHERE market_id = ? AND strategy_type = 'whale' AND summary LIKE '%series%'", (market_id_2,)).fetchone()
        assert sig_series is not None
        assert sig_series["status"] == "PENDING"
        assert sig_series["target_outcome"] == "NO"

    # Проверяем, что в whale_stocks_monitoring записались адреса китов
    with get_connection() as conn:
        monitoring_1 = conn.execute("SELECT wallet_address FROM whale_stocks_monitoring WHERE market_id = ?", (market_id_1,)).fetchone()
        assert monitoring_1 is not None
        assert monitoring_1["wallet_address"] == "0xWhaleSingle"

        monitoring_2 = conn.execute("SELECT wallet_address FROM whale_stocks_monitoring WHERE market_id = ?", (market_id_2,)).fetchone()
        assert monitoring_2 is not None
        assert monitoring_2["wallet_address"] == "0xWhaleSeries"

    # 4. Проверяем досрочную резолюцию сигналов
    # Изменим исходы рынков в БД на "YES" (досрочное разрешение)
    with get_connection() as conn:
        conn.execute("UPDATE markets SET outcome = 'YES' WHERE id IN (?, ?)", (market_id_1, market_id_2))

    # Запускаем Outcome Tracker
    with patch("services.polymarket_client.get_market_resolution", return_value="YES"):
        stats = run_resolution_cycle()
        assert stats["resolved"] >= 2

    # Проверяем, что наши сигналы обновились
    with get_connection() as conn:
        sig_single = conn.execute("SELECT status, pnl_realized FROM signals WHERE market_id = ? AND summary LIKE '%single%'", (market_id_1,)).fetchone()
        assert sig_single["status"] == "WIN"
        assert sig_single["pnl_realized"] == pytest.approx(10.0)

        sig_series = conn.execute("SELECT status, pnl_realized FROM signals WHERE market_id = ? AND summary LIKE '%series%'", (market_id_2,)).fetchone()
        assert sig_series["status"] == "LOSS"
        assert sig_series["pnl_realized"] == pytest.approx(-10.0)


def test_sell_virtual_whale_stock_with_custom_sell_price():
    from agents.shared.python.db import add_whale_stock_to_monitoring, buy_virtual_whale_stock, sell_virtual_whale_stock, get_connection
    import uuid

    market_id = f"mkt_whale_sell_test_{uuid.uuid4().hex[:8]}"
    
    # 1. Добавляем рынок в мониторинг с начальной ценой 0.50 (исход YES)
    add_whale_stock_to_monitoring(
        market_id=market_id,
        title="Will Custom Sell Price Test succeed?",
        url="https://polymarket.com/custom-sell-test",
        initial_price=0.50,
        predicted_outcome="YES",
        edge=0.10,
        confidence=0.50
    )
    
    # 2. Покупаем по цене 0.45
    buy_virtual_whale_stock(market_id, 0.45)
    
    # 3. Продаем по цене 0.85, при этом в мониторинге current_price остаётся равен initial_price (0.50)
    sell_virtual_whale_stock(market_id, sell_price=0.85)
    
    # 4. Проверяем историю виртуальных сделок
    with get_connection() as conn:
        trade = conn.execute("SELECT * FROM whale_virtual_trades_history WHERE market_id = ?", (market_id,)).fetchone()
        assert trade is not None
        assert trade["bought_price"] == pytest.approx(0.45)
        # Так как направление YES, sold_outcome_price должна быть равна sell_price
        assert trade["sold_price"] == pytest.approx(0.85)
        assert trade["sold_outcome_price"] == pytest.approx(0.85)
        # PnL = 0.85 - 0.45 = 0.40
        assert trade["pnl_cents"] == pytest.approx(0.40)
        assert trade["pnl_percent"] == pytest.approx(88.89, abs=0.01) # (0.40 / 0.45) * 100
        
        # Проверяем, что в мониторинге сбросились параметры покупки
        monitoring = conn.execute("SELECT * FROM whale_stocks_monitoring WHERE market_id = ?", (market_id,)).fetchone()
        assert monitoring is not None
        assert monitoring["virtual_bought_price"] is None
        assert monitoring["virtual_bought_at"] is None


