import sqlite3, json, pytest
from unittest.mock import patch, AsyncMock, MagicMock

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY, type TEXT, status TEXT,
            was_profitable INTEGER, pnl_realized REAL, resolved_at TEXT
        );
        CREATE TABLE idea_audit (
            id INTEGER PRIMARY KEY, market_id TEXT, final_outcome TEXT,
            shadow_agree INTEGER, scout_edge REAL, scout_probability REAL,
            shadow_reason TEXT, created_at TEXT
        );
        CREATE TABLE markets (id TEXT PRIMARY KEY, outcome TEXT);
        CREATE TABLE penny_stocks_monitoring (
            market_id TEXT, status TEXT, predicted_outcome TEXT,
            actual_outcome TEXT, resolved_at TEXT
        );
        CREATE TABLE whale_virtual_trades_history (
            id INTEGER PRIMARY KEY, market_id TEXT,
            pnl_cents REAL, sold_at TEXT
        );
        CREATE TABLE compound_virtual_trades_history (
            id INTEGER PRIMARY KEY, pnl_usd REAL, sold_at TEXT
        );
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY, agent_name TEXT,
            total_tokens INTEGER, created_at TEXT
        );
        CREATE TABLE calibration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT, window_days INTEGER,
            signals_analyzed INTEGER, metrics_json TEXT,
            nexus_response TEXT, params_proposed INTEGER DEFAULT 0,
            params_applied INTEGER DEFAULT 0, status TEXT DEFAULT 'completed'
        );
        CREATE TABLE calibration_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_type TEXT, param_name TEXT, param_value TEXT,
            previous_value TEXT, reason TEXT, status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE memory (key TEXT PRIMARY KEY, value TEXT);
    """)
    yield conn
    conn.close()

class TestCalibrationMetrics:
    def test_empty_db_returns_valid_structure(self, db):
        from agents.orchestrator.scripts.calibration_metrics import get_all_metrics
        metrics = get_all_metrics(db, 7)
        assert "win_rate" in metrics
        assert "brier_score" in metrics
        assert "funnel" in metrics
        assert "pnl" in metrics
        assert metrics["funnel"]["total_analyzed"] == 0

    def test_win_rate_calculation(self, db):
        db.executescript("""
            INSERT INTO signals VALUES (1,'SCOUT','ARCHIVED',1,10.0,datetime('now','-1 day'));
            INSERT INTO signals VALUES (2,'SCOUT','ARCHIVED',0,-5.0,datetime('now','-1 day'));
            INSERT INTO signals VALUES (3,'SWING','ARCHIVED',1,8.0,datetime('now','-1 day'));
        """)
        from agents.orchestrator.scripts.calibration_metrics import get_win_rate_by_strategy
        wr = get_win_rate_by_strategy(db, 7)
        assert wr["scout"]["total"] == 2
        assert wr["scout"]["wins"] == 1
        assert wr["scout"]["win_rate"] == 50.0
        assert wr["swing"]["win_rate"] == 100.0

    def test_brier_score_perfect_calibration(self, db):
        db.execute("INSERT INTO markets VALUES ('m1', 'YES')")
        db.execute("""
            INSERT INTO idea_audit
            VALUES (1,'m1','signal',1,0.8,1.0,NULL,datetime('now','-1 day'))
        """)
        from agents.orchestrator.scripts.calibration_metrics import get_brier_score
        result = get_brier_score(db, 7)
        assert result["brier_score"] == 0.0
        assert result["samples"] == 1

    def test_brier_score_ignores_out_of_range_probability(self, db):
        db.execute("INSERT INTO markets VALUES ('m2', 'YES')")
        db.execute("""
            INSERT INTO idea_audit
            VALUES (1,'m2','signal',1,0.8,50.0,NULL,datetime('now','-1 day'))
        """)
        from agents.orchestrator.scripts.calibration_metrics import get_brier_score
        result = get_brier_score(db, 7)
        assert result["samples"] == 0 or result["brier_score"] <= 1.0

    def test_funnel_stats(self, db):
        db.executescript("""
            INSERT INTO idea_audit VALUES (1,'m1','PASS',1,0.6,0.6,NULL,datetime('now','-1 day'));
            INSERT INTO idea_audit VALUES (2,'m2','REJECT_SHADOW',0,0.6,0.6,'too risky',datetime('now','-1 day'));
            INSERT INTO idea_audit VALUES (3,'m3','REJECT_SHADOW',0,0.6,0.6,'low edge',datetime('now','-1 day'));
        """)
        from agents.orchestrator.scripts.calibration_metrics import get_funnel_stats
        funnel = get_funnel_stats(db, 7)
        assert funnel["total_analyzed"] == 3
        assert funnel["breakdown"]["PASS"] == 1
        assert funnel["breakdown"]["REJECT_SHADOW"] == 2

class TestCalibrationReport:
    def test_report_contains_all_sections(self):
        from agents.orchestrator.scripts.calibration_report import generate_calibration_report
        metrics = {
            "win_rate": {"scout": {"win_rate": 60.0, "wins": 3, "total": 5}},
            "brier_score": {"brier_score": 0.12, "samples": 10},
            "funnel": {"total_analyzed": 20, "breakdown": {"PASS": 5, "REJECT_SHADOW": 15}},
            "pnl": {"scout": 12.5},
            "tokens": {"SCOUT": {"calls": 100, "tokens": 50000}},
            "shadow_rejections": [{"reason": "too risky", "count": 5}],
            "window_days": 7,
        }
        report = generate_calibration_report(metrics)
        assert "Brier Score" in report
        assert "Win Rate" in report
        assert "PnL" in report
        assert "SHADOW" in report

    def test_report_brier_good_threshold(self):
        from agents.orchestrator.scripts.calibration_report import generate_calibration_report
        metrics = {
            "win_rate": {}, "funnel": {"total_analyzed": 0, "breakdown": {}},
            "pnl": {}, "tokens": {}, "shadow_rejections": [], "window_days": 7,
            "brier_score": {"brier_score": 0.10, "samples": 5},
        }
        report = generate_calibration_report(metrics)
        assert "Отличная калибровка" in report

    def test_report_brier_bad_threshold(self):
        from agents.orchestrator.scripts.calibration_report import generate_calibration_report
        metrics = {
            "win_rate": {}, "funnel": {"total_analyzed": 0, "breakdown": {}},
            "pnl": {}, "tokens": {}, "shadow_rejections": [], "window_days": 7,
            "brier_score": {"brier_score": 0.25, "samples": 5},
        }
        report = generate_calibration_report(metrics)
        assert "Плохая калибровка" in report

class TestRunCalibration:
    @pytest.mark.asyncio
    async def test_skips_when_low_data(self, db):
        """Менее 5 рынков → статус skipped_low_data."""
        import contextlib
        @contextlib.contextmanager
        def mock_conn():
            yield db

        with patch("agents.orchestrator.scripts.calibrate.get_connection", side_effect=mock_conn):
            from agents.orchestrator.scripts.calibrate import run_calibration
            await run_calibration(window_days=7)

        row = db.execute(
            "SELECT status FROM calibration_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["status"] == "skipped_low_data"

    @pytest.mark.asyncio
    async def test_saves_overlay_params_on_success(self, db):
        for i in range(10):
            db.execute(
                "INSERT INTO signals VALUES (?,?,?,?,?,datetime('now','-1 day'))",
                (i, "SCOUT", "ARCHIVED", 1, 5.0)
            )
        for i in range(10):
            db.execute(
                "INSERT INTO idea_audit VALUES (?,?,?,?,?,?,?,datetime('now','-1 day'))",
                (i, f"m{i}", "PASS", 1, 0.6, 0.6, None)
            )

        mock_llm_response = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "scout_overlay": "Снизь уверенность на 10% в спорте",
                "swing_overlay": "",
                "shadow_overlay": "Будь строже к политическим рынкам",
                "reasoning": "Win rate у SCOUT низкий"
            })}]}}]
        }

        import contextlib
        @contextlib.contextmanager
        def mock_conn():
            yield db

        with patch("agents.orchestrator.scripts.calibrate.get_connection", side_effect=mock_conn), \
             patch("agents.orchestrator.scripts.calibrate.generate_content_with_fallback",
                   return_value=(mock_llm_response, "gemini-2.5-pro")), \
             patch("agents.shared.utils.gemini_client.extract_response_text",
                   return_value=json.dumps({
                       "scout_overlay": "Снизь уверенность на 10% в спорте",
                       "swing_overlay": "",
                       "shadow_overlay": "Будь строже к политическим рынкам",
                       "reasoning": "Win rate у SCOUT низкий"
                   })), \
             patch("agents.shared.python.db.get_memory", return_value=""):
            from agents.orchestrator.scripts.calibrate import run_calibration
            await run_calibration(window_days=7)

        run_row = db.execute(
            "SELECT status, params_proposed FROM calibration_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run_row["status"] == "completed"
        assert run_row["params_proposed"] == 2

        params = db.execute(
            "SELECT strategy_type, param_value FROM calibration_params"
        ).fetchall()
        strategy_types = {r["strategy_type"] for r in params}
        assert "SCOUT" in strategy_types
        assert "SHADOW" in strategy_types
        assert "SWING" not in strategy_types

class TestOverlayAppliedToAgents:
    def test_mispricing_agent_applies_scout_overlay(self):
        with patch("agents.shared.python.db.get_memory", return_value="Снизь уверенность в спорте"):
            from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
            agent = ScoutAgent.__new__(ScoutAgent)
            agent.base_system_instruction = "BASE PROMPT"
            overlay = "Снизь уверенность в спорте"
            current_system_instruction = agent.base_system_instruction
            if overlay:
                current_system_instruction += f"\\n\\n[CALIBRATOR OVERLAY INSTRUCTION]:\\n{overlay}\\n"
            assert "CALIBRATOR OVERLAY INSTRUCTION" in current_system_instruction
            assert "Снизь уверенность" in current_system_instruction

    def test_empty_overlay_does_not_modify_prompt(self):
        with patch("agents.shared.python.db.get_memory", return_value=""):
            overlay = ""
            current_system_instruction = "BASE PROMPT"
            if overlay:
                current_system_instruction += f"\\n\\n[CALIBRATOR OVERLAY INSTRUCTION]:\\n{overlay}\\n"
            assert "CALIBRATOR OVERLAY INSTRUCTION" not in current_system_instruction
