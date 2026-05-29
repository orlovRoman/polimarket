# tests/test_workflow_consensus.py
from unittest.mock import MagicMock, patch
from core.workflow import process_consensus, make_consensus


def _make_market(price=0.46, url="https://polymarket.com/event/test"):
    m = MagicMock()
    m.id = "test-market-id"
    m.title = "Test Market"
    m.url = url
    m.price = price
    return m


def _make_signal(outcome="NO", verdict="Рынок переоценён"):
    s = MagicMock()
    s.edge = 0.12
    s.target_outcome = outcome
    s.signal_verdict = verdict
    s.signal_cause = verdict
    s.oracle_risk = ""
    return s


def _make_shadow(agree=True, liq="MEDIUM"):
    sh = MagicMock()
    sh.agree = agree
    sh.liquidity_risk = liq
    sh.orderbook_facts = "Bid $48k / Ask $87k"
    sh.confidence = 0.8
    sh.opinion = "ok"
    sh.shadow_verdict = "Вход безопасен"
    sh.risk_assessment = "ok"
    return sh


def _make_context(market, trigger_type="scheduled", source_url="", source_text=""):
    ctx = MagicMock()
    ctx.market = market
    ctx.trigger_type = trigger_type
    ctx.source_url = source_url
    ctx.source_text = source_text
    ctx.triggered_at = None
    ctx.math_filter_result = None
    return ctx


# ── 1. Сообщение отправляется при консенсусе или при его отсутствии (дебаты) ────────

@patch("core.workflow.make_consensus")
def test_no_callback_when_no_consensus(mock_make):
    mock_make.return_value = MagicMock(status='no_consensus')
    m = _make_market()
    ctx = _make_context(m)
    callback = MagicMock()
    
    # SHADOW против → no_consensus
    process_consensus(ctx, _make_signal(), None, _make_shadow(agree=False),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    callback.assert_called_once()
    text = callback.call_args[0][0]
    assert "Консенсус не достигнут" in text


@patch("core.workflow.make_consensus")
def test_no_callback_when_no_signal(mock_make):
    mock_make.return_value = MagicMock(status='no_signal')
    m = _make_market()
    ctx = _make_context(m)
    callback = MagicMock()
    
    process_consensus(ctx, None, None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    callback.assert_not_called()


@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_callback_called_when_consensus(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market()
    ctx = _make_context(m)
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    callback.assert_called_once()
    text = callback.call_args[0][0]
    assert "Обсуждение рынка:" in text


# ── 2. BUY direction формируется корректно ─────────────────

@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_buy_yes_signal(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market(price=0.54)
    ctx = _make_context(m)
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(outcome="YES"), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "YES: 54¢" in text


@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_buy_no_entry_price(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market(price=0.54)  # YES=54¢ → NO=46¢
    ctx = _make_context(m)
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(outcome="NO"), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "NO: 46¢" in text


# ── 3. (Устарело, price теперь напрямую берётся для отображения YES/NO) ──

@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_zero_price_guard(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market(price=0.0)
    ctx = _make_context(m)
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(outcome="YES"), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "YES: 0¢ | NO: 100¢" in text


# ── 4. source_url event-driven ──────────────────────────────

@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_source_url_shown_for_event_driven(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market()
    ctx = _make_context(m, trigger_type="event_driven",
                        source_url="https://t.me/somechannel/123",
                        source_text="Whale $15,000")
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "https://t.me/somechannel/123" in text
    assert "Whale $15,000" in text


@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_empty_source_url_no_degradation_warning(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    """При пустом source_url в event_driven — не должно быть 'деградация до scheduled'"""
    m = _make_market()
    ctx = _make_context(m, trigger_type="event_driven", source_url="")
    callback = MagicMock()
    
    process_consensus(ctx, _make_signal(), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "деградация до scheduled" not in text


# ── 5. Арбитраж из math_filter ──────────────────────────────

@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_arbitrage_block_shown_when_available(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market()
    ctx = _make_context(m)
    
    math_result = MagicMock()
    math_result.has_arbitrage = True
    math_result.spread_pct = 3.2
    math_result.trade_instruction = "BUY YES здесь 54¢ + BUY NO там 43¢ = профит 3¢"
    ctx.math_filter_result = math_result
    
    callback = MagicMock()
    process_consensus(ctx, _make_signal(), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "Арбитраж" in text
    assert "3.2%" in text
    assert "BUY YES здесь" in text


@patch("core.workflow.save_signal", create=True)
@patch("core.workflow.make_consensus")
def test_arbitrage_block_hidden_when_no_arbitrage(mock_make, mock_save_signal):
    mock_make.return_value = MagicMock(status='saved')
    m = _make_market()
    ctx = _make_context(m)
    
    math_result = MagicMock()
    math_result.has_arbitrage = False
    ctx.math_filter_result = math_result
    
    callback = MagicMock()
    process_consensus(ctx, _make_signal(), None, _make_shadow(),
                      state={}, update_state=MagicMock(), summary_callback=callback)
    
    text = callback.call_args[0][0]
    assert "Арбитраж" not in text
