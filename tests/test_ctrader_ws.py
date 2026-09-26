"""Tests for the cTrader JSON-over-WebSocket client (fake scripted frames)."""

import asyncio
import json
import types

import pytest

import app.execution.ctrader as ctrader
from app.execution.ctrader import (
    CTraderClient,
    CTraderCredentials,
    CTraderError,
)

CREDS = CTraderCredentials(client_id="cid-123", client_secret="sec-456", mode="demo")

# Script of frames the "server" returns next, echoed with the request clientMsgId.
_CANNED: list[dict] = []
_SENT: list[dict] = []


class _FakeWS:
    def __init__(self, script):
        self.script = script
        self.url = "wss://demo.ctraderapi.com:5036"
        self._last_msg_id = None

    async def send(self, text):
        frame = json.loads(text)
        _SENT.append(frame)
        if frame.get("clientMsgId"):
            self._last_msg_id = frame["clientMsgId"]

    async def recv(self):
        if not self.script:
            await asyncio.sleep(3600)
        frame = dict(self.script.pop(0))
        if frame.get("echo") and self._last_msg_id:
            frame["clientMsgId"] = self._last_msg_id
        return json.dumps(frame)

    async def close(self):
        pass


def _frame(payload_type, payload, *, echo=True):
    return {"echo": echo, "payloadType": payload_type, "payload": payload}


def _version():
    return _frame(2105, {"version": "1.0"})


def _install_fake_ws(monkeypatch):
    async def fake_connect(url, **_kw):
        assert isinstance(url, str) and url.startswith("wss://")
        return _FakeWS(_CANNED)

    fake_mod = types.SimpleNamespace(connect=fake_connect)
    monkeypatch.setattr(ctrader, "websockets", fake_mod)


def _client(monkeypatch, script, timeout=5.0, min_volume_units=0.0):
    _CANNED[:] = script
    _SENT[:] = []
    _install_fake_ws(monkeypatch)
    return CTraderClient(timeout=timeout, min_volume_units=min_volume_units)


def _sent_frame(payload_type):
    return next(f for f in _SENT if f.get("payloadType") == payload_type)


class TestGetAccounts:
    async def test_parses_trader_balance(self, monkeypatch):
        script = [
            _version(),
            _frame(2101, {}),
            _frame(
                2150,
                {"accessToken": "TOK", "ctidTraderAccount": [{"ctidTraderAccountId": 10012345}]},
            ),
            _frame(2103, {"ctidTraderAccountId": 10012345}),
            _frame(
                2122,
                {"ctidTraderAccountId": 10012345, "trader": {"balance": 123456, "moneyDigits": 2}},
            ),
        ]
        client = _client(monkeypatch, script)
        accounts = await client.get_accounts("TOK", creds=CREDS)
        assert len(accounts) == 1
        assert accounts[0].account_id == 10012345
        assert abs(accounts[0].balance - 1234.56) < 1e-9
        assert accounts[0].equity == accounts[0].balance
        assert _sent_frame(2149)["payload"]["accessToken"] == "TOK"
        assert _sent_frame(2121)["payload"]["ctidTraderAccountId"] == 10012345

    async def test_skips_accounts_that_fail_auth(self, monkeypatch):
        script = [
            _version(),
            _frame(2101, {}),
            _frame(
                2150,
                {"accessToken": "TOK", "ctidTraderAccount": [
                    {"ctidTraderAccountId": 999},
                    {"ctidTraderAccountId": 10012345},
                ]},
            ),
            # account 999 is unreachable on this host (e.g. demo/live mismatch)
            _frame(2142, {"errorCode": "CH_CTID_TRADER_ACCOUNT_NOT_FOUND", "description": "nope"}),
            _frame(2103, {"ctidTraderAccountId": 10012345}),
            _frame(
                2122,
                {"ctidTraderAccountId": 10012345, "trader": {"balance": 50000, "moneyDigits": 2}},
            ),
        ]
        client = _client(monkeypatch, script)
        accounts = await client.get_accounts("TOK", creds=CREDS)
        assert [a.account_id for a in accounts] == [10012345]
        assert abs(accounts[0].balance - 500.0) < 1e-9

    async def test_timeout_raises(self, monkeypatch):
        script = [_version(), _frame(2101, {})]
        client = _client(monkeypatch, script, timeout=0.6)
        with pytest.raises(CTraderError, match="timeout"):
            await client.get_accounts("TOK", creds=CREDS)

    async def test_app_auth_failure_raises(self, monkeypatch):
        script = [
            _version(),
            _frame(2142, {"errorCode": "FAILED_TO_AUTHENTICATE", "description": "bad creds"}),
        ]
        client = _client(monkeypatch, script)
        with pytest.raises(CTraderError, match="FAILED_TO_AUTHENTICATE"):
            await client.get_accounts("TOK", creds=CREDS)


class TestGetPositions:
    async def test_resolves_symbol_names(self, monkeypatch):
        script = [
            _version(),
            _frame(2101, {}),
            _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2125,
                {
                    "ctidTraderAccountId": 123,
                    "position": [
                        {
                            "positionId": 77,
                            "price": 60000.0,
                            "tradeData": {"symbolId": 111, "volume": 100, "tradeSide": 1},
                        }
                    ],
                },
            ),
            _frame(
                2115,
                {"ctidTraderAccountId": 123, "symbol": [{"symbolId": 111, "symbolName": "BTCUSD"}]},
            ),
        ]
        client = _client(monkeypatch, script)
        positions = await client.get_positions("TOK", creds=CREDS, account_id=123)
        assert len(positions) == 1
        assert positions[0].position_id == "77"
        assert positions[0].symbol == "BTCUSD"
        assert positions[0].trade_side == "Buy"
        assert abs(positions[0].volume - 1.0) < 1e-9  # 100/100
        assert positions[0].price == 60000.0


class TestResolveSymbolId:
    async def test_normalizes_symbol(self, monkeypatch):
        script = [
            _version(), _frame(2101, {}),
            _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2115,
                {"ctidTraderAccountId": 123, "symbol": [{"symbolId": 111, "symbolName": "BTC/USD"}]},
            ),
            # second connection: cache miss for BTC-USDT -> fresh symbol list
            _version(), _frame(2101, {}),
            _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2115,
                {"ctidTraderAccountId": 123, "symbol": [{"symbolId": 222, "symbolName": "GBP/USD"}]},
            ),
        ]
        client = _client(monkeypatch, script)
        assert await client.resolve_symbol_id("TOK", "BTCUSD", creds=CREDS, account_id=123) == 111
        assert await client.resolve_symbol_id("TOK", "BTC-USDT", creds=CREDS, account_id=123) is None


class TestPlaceMarketOrder:
    async def test_rejects_below_minimum_volume(self, monkeypatch):
        client = _client(monkeypatch, [], min_volume_units=1000.0)
        with pytest.raises(CTraderError, match="minimum"):
            await client.place_market_order(
                access_token="TOK",
                creds=CREDS,
                account_id=123,
                symbol_id=111,
                side="Buy",
                volume=438.63,
                stop_loss=1.09,
            )
        assert not any(f.get("payloadType") == 2106 for f in _SENT)

    async def test_market_then_sltp_amend(self, monkeypatch):
        script = [
            _version(),
            _frame(2101, {}),
            _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2126,
                {
                    "ctidTraderAccountId": 123,
                    "executionType": 3,
                    "order": {"orderId": 900, "clientOrderId": "x"},
                    "position": {"positionId": 555},
                },
            ),
            _frame(2126, {"ctidTraderAccountId": 123, "executionType": 4, "order": {"orderId": 900}}),
        ]
        client = _client(monkeypatch, script)
        reply = await client.place_market_order(
            access_token="TOK",
            creds=CREDS,
            account_id=123,
            symbol_id=111,
            side="Buy",
            volume=2.5,
            stop_loss=58000.0,
            take_profit=62000.0,
        )
        assert reply.order_id == "900"
        order_req = _sent_frame(2106)
        assert order_req["payload"]["orderType"] == 1
        assert order_req["payload"]["tradeSide"] == 1
        assert order_req["payload"]["volume"] == 250  # 2.5 units -> 0.01-unit protocol
        assert order_req["payload"]["clientOrderId"]
        assert "stopLoss" not in order_req["payload"]
        assert "takeProfit" not in order_req["payload"]
        assert _sent_frame(2110)["payload"] == {"positionId": 555, "stopLoss": 58000.0, "takeProfit": 62000.0}

    async def test_rejected_order_raises(self, monkeypatch):
        script = [
            _version(),
            _frame(2101, {}),
            _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(2126, {"ctidTraderAccountId": 123, "executionType": 7, "description": "no liquidity"}),
        ]
        client = _client(monkeypatch, script)
        with pytest.raises(CTraderError, match="rejected"):
            await client.place_market_order(
                access_token="TOK",
                creds=CREDS,
                account_id=123,
                symbol_id=111,
                side="Sell",
                volume=0.5,
                stop_loss=70000.0,
            )


class TestClosePosition:
    async def test_discover_volume_then_close(self, monkeypatch):
        script = [
            _version(), _frame(2101, {}), _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2125,
                {"ctidTraderAccountId": 123,
                 "position": [{"positionId": 77, "price": 1.0,
                               "tradeData": {"symbolId": 1, "volume": 50, "tradeSide": 2}}]},
            ),
            _frame(2115, {"ctidTraderAccountId": 123, "symbol": [{"symbolId": 1, "symbolName": "EURUSD"}]}),
            _version(), _frame(2101, {}), _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(2126, {"ctidTraderAccountId": 123, "executionType": 3, "order": {"orderId": 901}}),
        ]
        client = _client(monkeypatch, script)
        await client.close_position("TOK", creds=CREDS, account_id=123, position_id="77")
        assert _sent_frame(2111)["payload"] == {"ctidTraderAccountId": 123, "positionId": 77, "volume": 50}


class TestPendingOrders:
    async def test_lists_working_orders(self, monkeypatch):
        script = [
            _version(), _frame(2101, {}), _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(
                2125,
                {"ctidTraderAccountId": 123, "order": [
                    {"orderId": 4040, "symbolId": 111, "volume": 250, "tradeSide": 1,
                     "clientOrderId": "cw-x", "positionId": 0},
                    {"orderId": 4050, "positionId": 77},
                ]},
            ),
        ]
        client = _client(monkeypatch, script)
        orders = await client.get_pending_orders("TOK", creds=CREDS, account_id=123)
        assert [o["order_id"] for o in orders] == [4040, 4050]
        assert orders[0]["volume"] == 2.5
        assert orders[0]["side"] == "Buy"
        assert orders[0]["position_id"] == 0
        assert orders[1]["position_id"] == 77


class TestCancelOrder:
    async def test_cancels_pending_order(self, monkeypatch):
        # Events may carry no clientMsgId; correlation must work via orderId.
        script = [
            _version(), _frame(2101, {}), _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(2126, {"ctidTraderAccountId": 123, "executionType": 5,
                          "order": {"orderId": 4040}}, echo=False),
        ]
        client = _client(monkeypatch, script)
        await client.cancel_order("TOK", creds=CREDS, account_id=123, order_id=4040)
        assert _sent_frame(2108)["payload"] == {"ctidTraderAccountId": 123, "orderId": 4040}

    async def test_cancel_rejected_raises(self, monkeypatch):
        script = [
            _version(), _frame(2101, {}), _frame(2103, {"ctidTraderAccountId": 123}),
            _frame(2126, {"ctidTraderAccountId": 123, "executionType": 8,
                          "order": {"orderId": 4040},
                          "description": "already filled"}, echo=False),
        ]
        client = _client(monkeypatch, script)
        with pytest.raises(CTraderError, match="rejected"):
            await client.cancel_order("TOK", creds=CREDS, account_id=123, order_id=4040)