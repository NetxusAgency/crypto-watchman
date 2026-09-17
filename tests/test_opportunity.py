from app.services.opportunity.engine import (
    format_opportunities,
    format_opportunity,
    priority_for,
    total_from_components,
)
from app.services.opportunity.signals import OpportunityComponent
from app.services.exchange_monitor.listing_monitor import _base_symbol


def comp(source, direction, strength, weight=None):
    c = OpportunityComponent(source, direction, strength, "test evidence")
    return c


class TestPriority:
    def test_thresholds(self):
        assert priority_for(0) == "LOW"
        assert priority_for(15) == "LOW"
        assert priority_for(20) == "MEDIUM"
        assert priority_for(39) == "MEDIUM"
        assert priority_for(40) == "HIGH"
        assert priority_for(59) == "HIGH"
        assert priority_for(60) == "CRITICAL"
        assert priority_for(88) == "CRITICAL"
        assert priority_for(-45) == "HIGH"  # abs used


class TestWeighting:
    def test_bullish_composite(self):
        out, direction = total_from_components(
            [
                OpportunityComponent("news", 1, 0.8, "x"),  # +16
                OpportunityComponent("volume", 1, 1.0, "x"),  # +20
                OpportunityComponent("whale", 1, 1.0, "x"),  # +10
                OpportunityComponent("liquidity", 0, 1.0, "x"),  # 0
            ]
        )
        assert out == 46.0
        assert direction == 1

    def test_bearish_flips_direction(self):
        out, direction = total_from_components(
            [OpportunityComponent("news", -1, 1.0, "x")]  # -20
        )
        assert out == -20.0
        assert direction == -1

    def test_clamped_at_100(self):
        out, _ = total_from_components(
            [OpportunityComponent(s, 1, 1.0, "x") for s in
             ("news", "volume", "momentum", "social", "listing", "whale", "liquidity")]
        )
        assert out <= 100.0


class TestComponentScore:
    def test_score_is_weighted(self):
        c = OpportunityComponent("news", 1, 0.5, "x")
        assert c.weight == 20
        assert c.score == 10.0

    def test_bearish_negative(self):
        c = OpportunityComponent("whale", -1, 0.75, "x")
        assert c.score == -7.5

    def test_neutral_zero(self):
        c = OpportunityComponent("volume", 0, 0.9, "x")
        assert c.score == 0.0


class TestFormatting:
    def _item(self, total=46, direction=1, priority="HIGH"):
        return {
            "symbol": "BTC",
            "total_score": total,
            "direction": direction,
            "priority": priority,
            "components": [
                {"source": "news", "score": 16.0, "evidence": "sentiment"},
                {"source": "volume", "score": 20.0, "evidence": "surge"},
                {"source": "whale", "score": 10.0, "evidence": "flow"},
            ],
            "evidence": "test",
        }

    def test_format_single(self):
        text = format_opportunity(self._item())
        assert "BTC" in text
        assert "+46" in text
        assert "HIGH" in text
        assert "news" in text

    def test_format_list_empty(self):
        assert "No opportunities" in format_opportunities([])

    def test_format_list_with_ai_note(self):
        text = format_opportunities([self._item()], ai_note="Strong setup, watch $100k.")
        assert "Strong setup" in text
        assert "news 20" in text


class TestListingBaseSymbol:
    def test_binance_usdt_suffix(self):
        assert _base_symbol("BTCUSDT", "Binance") == "BTC"
        assert _base_symbol("PEPEUSDC", "Binance") == "PEPE"

    def test_coinbase_dash(self):
        assert _base_symbol("BTC-USD", "Coinbase") == "BTC"
        assert _base_symbol("ETH-USD", "Coinbase") == "ETH"

    def test_perps_and_unknown(self):
        assert _base_symbol("SOLUSDT", "Bybit") == "SOL"
        assert _base_symbol("XYZ", "Binance") == "XYZ"