from app.execution.paper import CloseEvent, PaperExecutionEngine, PaperPositionState
from app.services.trading.risk import RiskReport
from app.services.trading.trade_plan import TradePlan, TradePlanStage


def _plan(direction="LONG", symbol="BTC", stop=95.0, entry_low=99.5, entry_high=100.5, tp1=108.0):
    return TradePlan(
        plan_id="TP-P1",
        symbol=symbol,
        direction=direction,
        timeframe="4h",
        strategy_key="trend_pullback",
        strategy_name="Trend",
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        take_profits=[tp1, tp1 + 4.0],
        invalidation="x",
        risk_reward=1.7,
        stage=TradePlanStage.SETUP_FOUND,
        context={"snapshot": {"current_price": entry_high}, "user_id": 1, "latest_candle_age_hrs": 1.0},
    )


def _ok_risk(plan):
    return RiskReport(plan_id=plan.plan_id, passed=True, checks=[], blocked_reasons=[])


def _bad_risk(plan):
    return RiskReport(plan_id=plan.plan_id, passed=False, checks=[], blocked_reasons=["risk-budget-exceeded"])


class TestPaperExecutionEngine:
    def setup_method(self):
        self.engine = PaperExecutionEngine(initial_balance=10_000.0)
        self.state = self.engine.fresh_state()

    def test_fresh_state(self):
        assert self.state.cash == 10_000.0
        assert self.state.equity == 10_000.0
        assert self.state.open_count == 0

    def test_open_long_fills(self):
        plan = _plan()
        fill, rejected = self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        assert rejected == []
        assert fill is not None
        assert fill.direction == "LONG"
        assert self.state.open_count == 1
        assert self.state.cash < 10_000.0  # entry fee deducted

    def test_risk_rejection_blocks_execution(self):
        plan = _plan()
        fill, rejected = self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_bad_risk(plan))
        assert fill is None
        assert "risk-budget-exceeded" in rejected
        assert self.state.open_count == 0

    def test_price_outside_zone_rejected(self):
        plan = _plan()
        fill, rejected = self.engine.open_position(self.state, plan, market_price=102.0, risk_report=_ok_risk(plan))
        assert fill is None
        assert any("outside-entry-zone" in r for r in rejected)

    def test_duplicate_symbol_rejected(self):
        plan = _plan(symbol="BTC")
        self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        again = _plan(symbol="BTC")
        fill, rejected = self.engine.open_position(self.state, again, market_price=99.8, risk_report=_ok_risk(again))
        assert fill is None
        assert "duplicate-position" in rejected

    def test_tp1_closes_with_profit(self):
        plan = _plan()
        self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        events = self.engine.mark(self.state, {"BTC": 109.0})
        assert len(events) == 1
        assert events[0].reason == "TP1"
        assert events[0].pnl > 0
        assert self.state.open_count == 0
        assert self.state.cash > 10_000.0

    def test_stop_closes_with_loss(self):
        plan = _plan()
        self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        events = self.engine.mark(self.state, {"BTC": 94.0})
        assert len(events) == 1
        assert events[0].reason == "STOP"
        assert events[0].pnl < 0

    def test_short_tp_below(self):
        plan = _plan(direction="SHORT", stop=104.0, entry_low=99.5, entry_high=100.5, tp1=92.0)
        self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        events = self.engine.mark(self.state, {"BTC": 91.0})
        assert len(events) == 1
        assert events[0].reason == "TP1"
        assert events[0].pnl > 0

    def test_no_trigger_no_close(self):
        plan = _plan()
        self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        events = self.engine.mark(self.state, {"BTC": 102.0})
        assert events == []
        assert self.state.open_count == 1

    def test_fee_taken_on_close(self):
        plan = _plan()
        fill, _ = self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        events = self.engine.mark(self.state, {"BTC": 109.0})
        expected_fee = 108.0 * fill.quantity * (5.0 / 10_000.0)
        assert events[0].fee == expected_fee

    def test_stats_win_rate_and_profit_factor(self):
        plan = _plan()
        events = [
            CloseEvent("BTC", "p1", "TP1", 100, 110, 1, "LONG", 10.0, 0.0),
            CloseEvent("BTC", "p2", "TP1", 100, 108, 1, "LONG", 8.0, 0.0),
            CloseEvent("BTC", "p3", "STOP", 100, 96, 1, "LONG", -4.0, 0.0),
        ]
        stats = self.engine.stats(events)
        assert stats["closed"] == 3
        assert stats["win_rate"] == round(2 / 3 * 100, 2)
        assert stats["profit_factor"] == round(18.0 / 4.0, 2)

    def test_position_sizing_honours_risk_budget(self):
        plan = _plan(stop=95.0, entry_high=100.0)
        fill, _ = self.engine.open_position(self.state, plan, market_price=100.0, risk_report=_ok_risk(plan))
        risk_dollars = (100.0 - 95.0) * fill.quantity
        assert risk_dollars <= 200.0  # 2% of $10k
        assert 100.0 * fill.quantity <= 9_500.0

    def test_unrealised_markup(self):
        pos = PaperPositionState(
            plan_id="p", symbol="BTC", direction="LONG", strategy_key="k", timeframe="4h",
            entry_price=100.0, quantity=2.0, stop_loss=95.0, take_profit_1=110.0, take_profit_2=None,
        )
        assert pos.unrealised_pnl(105.0) == 10.0
        assert pos.unrealised_pct(110.0) == 10.0