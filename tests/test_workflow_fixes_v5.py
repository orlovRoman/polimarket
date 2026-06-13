import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import logging
from datetime import datetime, timezone

def _make_market(market_id="mkt-1", price=0.65):
    m = MagicMock()
    m.id = market_id
    m.title = "Will test happen?"
    m.url = "https://polymarket.com/test"
    m.price = price
    return m

def _make_context(market=None, trigger_type="scheduled"):
    ctx = MagicMock()
    ctx.market = market or _make_market()
    ctx.trigger_type = trigger_type
    ctx.source_url = ""
    ctx.source_text = ""
    ctx.math_filter_result = None
    ctx.correlation_hint = None
    return ctx

def _make_signal(edge=0.12, cause="Strong signal", risk="Low risk", verdict="BUY YES"):
    s = MagicMock()
    s.id = "sig-1"
    s.market_id = "mkt-1"
    s.target_outcome = "YES"
    s.priority = "HIGH"
    s.platform = "polymarket"
    s.confidence = 0.85
    s.edge = edge
    s.signal_cause = cause
    s.signal_risk = risk
    s.signal_verdict = verdict
    s.oracle_risk = ""
    s.summary = "Summary text"
    s.details = "Detail text"
    s.trade_action = "BUY YES"
    return s

def _make_shadow(agree=True, confidence=0.85):
    sh = MagicMock()
    sh.agree = agree
    sh.confidence = confidence
    sh.liquidity_risk = "LOW"
    sh.orderbook_facts = "Deep book"
    sh.risk_assessment = "Minimal slippage"
    sh.shadow_verdict = "APPROVE"
    sh.opinion = "Opinion text"
    return sh

# 1. test_consensus_checkpoint_actually_saves: проверяет, что save_checkpoint реально вызывается в цикле и при успешной верификации цикл прерывается.
def test_consensus_checkpoint_actually_saves():
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint") as mock_save, \
         patch("core.checkpoint.verify_checkpoint") as mock_verify:
        
        mock_make.return_value = MagicMock(status='saved')
        
        # Первая попытка verify_checkpoint вернет False, вторая True
        mock_verify.side_effect = [False, True]
        
        process_consensus(ctx, signal, None, shadow, state, update_state, None)
        
        # save_checkpoint должен быть вызван 2 раза
        assert mock_save.call_count == 2
        # verify_checkpoint должен быть вызван 2 раза
        assert mock_verify.call_count == 2

# 2. test_arbitrage_instruction_includes_price: проверяет, что при пустой trade_instruction срабатывает fallback и подтягивается последняя цена из БД.
def test_arbitrage_instruction_includes_price():
    from core.workflow import process_consensus

    ctx = _make_context()
    # Настраиваем math_filter_result с арбитражем, но с пустой trade_instruction
    math_res = MagicMock()
    math_res.has_arbitrage = True
    math_res.trade_instruction = ""
    math_res.spread_pct = 4.5
    ctx.math_filter_result = math_res

    signal = _make_signal()
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()
    
    received_text = []
    def callback(text, **kwargs):
        received_text.append(text)

    with patch("core.workflow.make_consensus") as mock_make, \
         patch("core.workflow.save_signal"), \
         patch("core.workflow.save_idea_audit"), \
         patch("agents.shared.python.db.save_agent_episode"), \
         patch("core.checkpoint.save_checkpoint"), \
         patch("core.checkpoint.verify_checkpoint", return_value=True), \
         patch("agents.shared.python.db.get_last_analyzed_price", return_value=0.585) as mock_get_price:
        
        mock_make.return_value = MagicMock(status='saved')
        process_consensus(ctx, signal, None, shadow, state, update_state, callback)
        
        mock_get_price.assert_called_once_with(ctx.market.id)
        assert len(received_text) == 1
        # Проверяем, что в итоговом тексте содержится fallback-инструкция с ценой
        assert "Купить YES (арбитраж) при цене 0.585" in received_text[0]

# 3. test_checkpoint_error_logs_stacktrace: проверяет, что при исключении в save_checkpoint выводится traceback в логи с уровнем ERROR.
def test_checkpoint_error_logs_stacktrace(caplog):
    from core.workflow import process_consensus

    ctx = _make_context()
    signal = _make_signal()
    shadow = _make_shadow(agree=True)
    state = {"ideas_found": 0}
    update_state = MagicMock()

    import logging
    log_parent = logging.getLogger("NexusPolyBot")
    orig_propagate = log_parent.propagate
    log_parent.propagate = True
    try:
        with patch("core.workflow.make_consensus") as mock_make, \
             patch("core.workflow.save_signal"), \
             patch("core.workflow.save_idea_audit"), \
             patch("agents.shared.python.db.save_agent_episode"), \
             patch("core.checkpoint.save_checkpoint", side_effect=RuntimeError("IO Error on disk")), \
             patch("core.checkpoint.verify_checkpoint", return_value=False):
            
            mock_make.return_value = MagicMock(status='saved')
            
            with caplog.at_level(logging.ERROR, logger="NexusPolyBot"):
                process_consensus(ctx, signal, None, shadow, state, update_state, None)
                
            # Лог должен содержать сообщение об ошибке сохранения с уровнем ERROR и тегом [CHECKPOINT]
            error_records = [r for r in caplog.records if r.levelname == "ERROR" and "[CHECKPOINT]" in r.message]
            assert len(error_records) > 0
            assert any(r.exc_info is not None for r in error_records)
    finally:
        log_parent.propagate = orig_propagate

# 4. test_cache_type_reset_writes_empty_list: проверяет сброс невалидного кэша (если в кэше строка вместо списка) в пустой список [] через save_memory.
def test_cache_type_reset_writes_empty_list():
    from core.workflow import run_screening
    
    now_iso = datetime.now(timezone.utc).isoformat()
    
    def mock_get_memory(key):
        if key == "last_screen_time":
            return now_iso
        if key == "screened_market_ids":
            return {"not": "a_list"}
        return None

    with patch("core.workflow.get_memory", side_effect=mock_get_memory), \
         patch("core.workflow.save_memory") as mock_save_mem:
        
        result = run_screening(MagicMock(), "", "")
        
        assert result == []
        mock_save_mem.assert_any_call("screened_market_ids", [], category='cache', ttl=1800)

# 5. test_bot_health_command_enhanced: проверяет вывод команды /health со статусом ротации ключей и стоимостью вызовов LLM.
@pytest.mark.anyio
async def test_bot_health_command_enhanced():
    from telegram.bot import command_health_handler
    
    message = MagicMock()
    message.answer = AsyncMock()
    
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            model_name TEXT NOT NULL,
            market_id TEXT,
            prompt TEXT,
            response TEXT,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            latency_ms INTEGER,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Вчерашние данные
    cursor.execute("""
        INSERT INTO llm_calls (agent_name, model_name, input_tokens, output_tokens, total_tokens, created_at)
        VALUES ('SCOUT', 'gemini-2.5-flash', 1000, 200, 1200, datetime('now', '-10 minutes'))
    """)
    cursor.execute("""
        INSERT INTO llm_calls (agent_name, model_name, input_tokens, output_tokens, total_tokens, created_at)
        VALUES ('SWING', 'gemini-2.5-pro', 500, 100, 600, datetime('now', '-2 hours'))
    """)
    # Данные 3 дня назад
    cursor.execute("""
        INSERT INTO llm_calls (agent_name, model_name, input_tokens, output_tokens, total_tokens, created_at)
        VALUES ('SHADOW', 'gemini-2.5-flash', 2000, 500, 2500, datetime('now', '-3 days'))
    """)
    conn.commit()
    
    def mock_get_connection():
        return conn

    from config import llm_health_gate
    
    with patch("agents.shared.python.db.get_connection", mock_get_connection), \
         patch("agents.shared.python.db.get_memory", return_value="1"), \
         patch.object(llm_health_gate, "state", "HEALTHY"), \
         patch.object(llm_health_gate, "error_timestamps", []), \
         patch.object(llm_health_gate, "retry_after", datetime.now(timezone.utc)), \
         patch("core.checkpoint._checkpoints_cache", {}), \
         patch("telegram.bot._scan_lock.locked", return_value=False):
         
        await command_health_handler(message)
        
    message.answer.assert_called_once()
    reply_text = message.answer.call_args[0][0]
    
    assert "🏥 <b>Здоровье системы:</b>" in reply_text
    assert "🔑 <b>Ротация ключей Gemini:</b>" in reply_text
    assert "📊 <b>Аналитика затрат API (24 часа):</b>" in reply_text
    assert "📆 <b>Аналитика затрат API (7 дней):</b>" in reply_text
    
    # SCOUT потратил больше всего за 24 часа (1200 против 600 у SWING)
    assert "SCOUT (1,200 токенов)" in reply_text
    # SHADOW потратил больше всего за неделю (1200 SCOUT + 600 SWING + 2500 SHADOW = 4300, SHADOW топ)
    assert "SHADOW (2,500 токенов)" in reply_text
    
    conn.close()
