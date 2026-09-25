"""cTrader Open API JSON-over-WebSocket adapter.

Thin, testable broker client speaking the official cTrader Open API JSON wire
protocol (websocket) instead of the legacy REST v2 host (connect.ctrader.com),
which is no longer reachable. It is a real broker client: it must ONLY ever be
reached after `LiveExecutionService` passes its guard and (for real-money
accounts) the user confirms the trade in Telegram.

Connection & protocol facts (verified against help.ctrader.com + the
spotware/openapi-proto-messages .proto files):

    envelope    {"clientMsgId": "...", "payloadType": 2106, "payload": {...}}
    transport   wss://demo.ctraderapi.com:5036  (demo)
                wss://live.ctraderapi.com:5036  (live)
    flow        version req (best-effort) -> application auth -> account auth
    correlation every response/event echoes the request's clientMsgId (envelope
                field, guaranteed at the envelope level); order events also carry
                clientOrderId, matching is done by clientMsgId first, then by
                clientOrderId.

Key payload types (ProtoOAPayloadType / ProtoPayloadType):
    51   heartbeat event          2100/2101 app auth req/res
    2102/2103 account auth        2104/2105 version req/res
    2106   new order req          2110   amend position SLTP req
    2111   close position req     2114/2115 symbols list req/res
    2121/2122 trader req/res      2124/2125 reconcile req/res
    2126   execution event        2142   error res
    2149/2150 get accounts by access token req/res

Volume is encoded in 0.01 units ("1000 in protocol means 10.00 units").
Balance/equity are fixed-point: real = raw / 10^moneyDigits (default 2).

Per the official docs both `stopLoss` and `takeProfit` are NOT supported on a
MARKET order, so a filled market order is followed by an
AmendPositionSLTPReq (2110) when a stop/take is requested.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import websockets

from app.core.config import settings

__all__ = [
    "CTraderAccount",
    "CTraderPosition",
    "CTraderOrderReply",
    "CTraderCredentials",
    "CTraderClient",
    "CTraderError",
]

logger = logging.getLogger("crypto_watchman.ctrader_ws")

# --- ProtoOAPayloadType / ProtoPayloadType (verified integers) ---
PT_HEARTBEAT_EVENT = 51
PT_ERROR_RES_COMMON = 50
PT_APPLICATION_AUTH_REQ = 2100
PT_APPLICATION_AUTH_RES = 2101
PT_ACCOUNT_AUTH_REQ = 2102
PT_ACCOUNT_AUTH_RES = 2103
PT_VERSION_REQ = 2104
PT_VERSION_RES = 2105
PT_NEW_ORDER_REQ = 2106
PT_AMEND_POSITION_SLTP_REQ = 2110
PT_CLOSE_POSITION_REQ = 2111
PT_SYMBOLS_LIST_REQ = 2114
PT_SYMBOLS_LIST_RES = 2115
PT_TRADER_REQ = 2121
PT_TRADER_RES = 2122
PT_RECONCILE_REQ = 2124
PT_RECONCILE_RES = 2125
PT_EXECUTION_EVENT = 2126
PT_ERROR_RES = 2142
PT_GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ = 2149
PT_GET_ACCOUNTS_BY_ACCESS_TOKEN_RES = 2150

# --- ProtoOAOrderType ---
ORDER_TYPE_MARKET = 1

# --- ProtoOATradeSide ---
TRADE_SIDE_BUY = 1
TRADE_SIDE_SELL = 2

# --- ProtoOAExecutionType (terminal state subsets) ---
EXEC_ORDER_ACCEPTED = 2
EXEC_ORDER_FILLED = 3
EXEC_ORDER_REPLACED = 4
EXEC_ORDER_CANCELLED = 5
EXEC_ORDER_EXPIRED = 6
EXEC_ORDER_REJECTED = 7

_ORDER_FILL_OK = {EXEC_ORDER_FILLED}
_ORDER_FILL_FAIL = {EXEC_ORDER_REJECTED, EXEC_ORDER_CANCELLED, EXEC_ORDER_EXPIRED}
_AMEND_OK = {EXEC_ORDER_FILLED, EXEC_ORDER_REPLACED}
_AMEND_FAIL = {EXEC_ORDER_REJECTED, EXEC_ORDER_CANCELLED, EXEC_ORDER_EXPIRED}

_ERROR_PAYLOAD_TYPES = frozenset({PT_ERROR_RES, PT_ERROR_RES_COMMON})

# How long between client heartbeats while an order waits for its execution
# event. The docs ask to keep sending one every 10s on an idle connection.
_HEARTBEAT_INTERVAL_SECONDS = 9.0
_VERSION_HANDSHAKE_TIMEOUT_SECONDS = 3.0


class CTraderError(RuntimeError):
    pass


@dataclass
class CTraderAccount:
    account_id: int
    balance: float
    equity: float
    currency: str = "USD"


@dataclass
class CTraderPosition:
    position_id: str
    symbol: str
    trade_side: str  # Buy / Sell
    volume: float
    price: float


@dataclass
class CTraderOrderReply:
    order_id: str
    status: str = "Placed"


@dataclass
class CTraderCredentials:
    """The Open API application credentials + environment used to authenticate."""

    client_id: str
    client_secret: str
    mode: str = "demo"  # demo | live


@dataclass
class CTraderPayload:
    """What a client sent to the broker — captured on dry runs for tests/audit."""

    url: str
    headers: dict = field(default_factory=dict)
    json: dict | None = None


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("/", "").replace("-", "")


def _volume_to_proto(units: float) -> int:
    """Human units (10.00) -> protocol volume (1000)."""
    return max(1, int(round(float(units) * 100.0)))


def _format_error(frame: dict) -> str:
    body = frame.get("payload") or {}
    code = str(body.get("errorCode") or "?")
    desc = str(body.get("description") or "").strip()
    return f"cTrader error {code}".rstrip() + (f": {desc}" if desc else "")


class CTraderClient:
    def __init__(self, *, timeout: float | None = None, **_: object):
        self._timeout = timeout if timeout is not None else settings.CTRADER_WS_TIMEOUT_SECONDS
        # symbolId -> symbolName per account, kept across connections (products
        # rarely change, and resolve_symbol_id/get_positions both need it).
        self._symbol_cache: dict[int, dict[int, str]] = {}
        # Last payload sent; tests inspect this for dry-run assertions.
        self.last_payload: CTraderPayload | None = None

    # ----- connection plumbing -------------------------------------------------

    @staticmethod
    def _ws_url(creds: CTraderCredentials) -> str:
        default = settings.CTRADER_WS_DEMO_URL or "wss://demo.ctraderapi.com:5036"
        if (creds.mode or "demo") == "live":
            default = settings.CTRADER_WS_LIVE_URL or "wss://live.ctraderapi.com:5036"
        return default.rstrip("/")

    async def _connect(self, creds: CTraderCredentials):
        ws = await websockets.connect(
            self._ws_url(creds),
            open_timeout=20.0,
            close_timeout=5.0,
            max_size=16 * 1024 * 1024,
            ping_interval=None,
        )
        try:
            # Version handshake is best-effort: some proxies expect it first,
            # but a missing/mismatched reply must not block authentication.
            try:
                await self._request(
                    ws, PT_VERSION_REQ, {}, expect={PT_VERSION_RES}, timeout=_VERSION_HANDSHAKE_TIMEOUT_SECONDS
                )
            except CTraderError as exc:
                logger.debug("cTrader version handshake skipped: %s", exc)
            await self._request(
                ws,
                PT_APPLICATION_AUTH_REQ,
                {"clientId": creds.client_id, "clientSecret": creds.client_secret},
                expect={PT_APPLICATION_AUTH_RES},
                timeout=max(self._timeout, 25.0),
            )
        except CTraderError as exc:
            await ws.close()
            raise CTraderError(f"cTrader application auth failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            await ws.close()
            raise CTraderError(f"cTrader unreachable: {exc}") from exc
        return ws

    async def _account_auth(self, ws, account_id: int, access_token: str) -> None:
        await self._request(
            ws,
            PT_ACCOUNT_AUTH_REQ,
            {"ctidTraderAccountId": int(account_id), "accessToken": access_token},
            expect={PT_ACCOUNT_AUTH_RES},
            timeout=self._timeout,
        )

    @asynccontextmanager
    async def _session(self, creds: CTraderCredentials, *, access_token: str | None = None,
                       account_id: int | None = None):
        ws = await self._connect(creds)
        try:
            if account_id is not None:
                await self._account_auth(ws, account_id, access_token or "")
            yield ws
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _send(self, ws, payload_type: int, payload: dict, msg_id: str) -> None:
        frame = {"clientMsgId": msg_id, "payloadType": payload_type, "payload": payload or {}}
        self.last_payload = CTraderPayload(url=str(getattr(ws, "url", "")), json=dict(frame))
        await ws.send(json.dumps(frame))

    async def _recv(self, ws, timeout: float) -> dict:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        return json.loads(raw)

    @staticmethod
    def _matches(frame: dict, msg_id: str, client_order_id: str | None) -> bool:
        if frame.get("clientMsgId") == msg_id:
            return True
        if not client_order_id:
            return False
        order = (frame.get("payload") or {}).get("order") or {}
        return str(order.get("clientOrderId") or "") == str(client_order_id)

    async def _request(self, ws, payload_type: int, payload: dict, *, expect: set[int],
                       timeout: float) -> dict:
        """One request -> one response (or error/timeout), corr. by clientMsgId."""
        msg_id = secrets.token_urlsafe(12)
        await self._send(ws, payload_type, payload, msg_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CTraderError(
                    f"cTrader timeout: no response to {payload_type} within {timeout:g}s"
                )
            try:
                frame = await self._recv(ws, remaining)
            except asyncio.TimeoutError:
                raise CTraderError(
                    f"cTrader timeout: no response to {payload_type} within {timeout:g}s"
                ) from None
            if not self._matches(frame, msg_id, None):
                continue
            pt = frame.get("payloadType")
            body = frame.get("payload") or {}
            if pt in _ERROR_PAYLOAD_TYPES:
                raise CTraderError(_format_error(frame))
            if pt in expect:
                return frame
            raise CTraderError(f"cTrader unexpected response type {pt} for {payload_type}")

    async def _request_execution(
        self,
        ws,
        payload_type: int,
        payload: dict,
        *,
        ok_types: set[int],
        fail_types: set[int],
        timeout: float | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        """Send a trading request and wait for its terminal execution event.

        Intermediate events (ORDER_ACCEPTED etc.) are skipped; terminal events in
        `ok_types` are returned, terminal events in `fail_types` raise. A client
        heartbeat is sent whenever the proxy goes quiet for ~9s.
        """
        timeout = timeout if timeout is not None else self._timeout
        msg_id = secrets.token_urlsafe(12)
        await self._send(ws, payload_type, payload, msg_id)
        deadline = time.monotonic() + timeout
        last_heartbeat = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CTraderError(
                    f"cTrader timeout: no execution result for {payload_type} within {timeout:g}s"
                )
            if time.monotonic() - last_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                last_heartbeat = time.monotonic()
                try:
                    await self._send(ws, PT_HEARTBEAT_EVENT, {}, "hb")
                except Exception:  # noqa: BLE001
                    pass
            try:
                frame = await self._recv(ws, min(remaining, _HEARTBEAT_INTERVAL_SECONDS))
            except asyncio.TimeoutError:
                continue
            if not self._matches(frame, msg_id, client_order_id):
                continue
            pt = frame.get("payloadType")
            body = frame.get("payload") or {}
            if pt in _ERROR_PAYLOAD_TYPES:
                raise CTraderError(_format_error(frame))
            if pt != PT_EXECUTION_EVENT:
                continue
            execution_type = body.get("executionType")
            if execution_type in ok_types:
                return body
            if execution_type in fail_types:
                raise CTraderError(
                    f"cTrader order rejected (executionType={execution_type}): "
                    f"{body.get('errorCode') or ''} {body.get('description') or ''}".strip()
                )
            # ORDER_ACCEPTED / ORDER_PARTIAL_FILL etc. -> keep waiting.

    # ----- broker operations ---------------------------------------------------

    async def get_accounts(self, access_token: str, *, creds: CTraderCredentials) -> list[CTraderAccount]:
        """List accounts + balance (fixed-point / 10^moneyDigits) for a token."""
        accounts: list[CTraderAccount] = []
        async with self._session(creds) as ws:
            resp = await self._request(
                ws,
                PT_GET_ACCOUNTS_BY_ACCESS_TOKEN_REQ,
                {"accessToken": access_token},
                expect={PT_GET_ACCOUNTS_BY_ACCESS_TOKEN_RES},
                timeout=self._timeout,
            )
            raw_accounts = (resp.get("payload") or {}).get("ctidTraderAccount", []) or []
            for acc in raw_accounts:
                account_id = int(acc.get("ctidTraderAccountId") or 0)
                if not account_id:
                    continue
                try:
                    await self._request(
                        ws,
                        PT_ACCOUNT_AUTH_REQ,
                        {"ctidTraderAccountId": account_id, "accessToken": access_token},
                        expect={PT_ACCOUNT_AUTH_RES},
                        timeout=self._timeout,
                    )
                    trader_res = await self._request(
                        ws,
                        PT_TRADER_REQ,
                        {"ctidTraderAccountId": account_id},
                        expect={PT_TRADER_RES},
                        timeout=self._timeout,
                    )
                except CTraderError as exc:
                    logger.warning(
                        "get_accounts: account %s not usable on the %s host (%s); skipping",
                        account_id,
                        creds.mode,
                        exc,
                    )
                    continue
                trader = (trader_res.get("payload") or {}).get("trader") or {}
                digits = int(trader.get("moneyDigits") or 2)
                balance = float(trader.get("balance") or 0) / (10 ** digits)
                accounts.append(
                    CTraderAccount(account_id=account_id, balance=balance, equity=balance)
                )
        return accounts

    async def _symbol_map(self, ws, account_id: int) -> dict[int, str]:
        cached = self._symbol_cache.get(account_id)
        if cached:
            return cached
        resp = await self._request(
            ws,
            PT_SYMBOLS_LIST_REQ,
            {"ctidTraderAccountId": int(account_id)},
            expect={PT_SYMBOLS_LIST_RES},
            timeout=self._timeout,
        )
        mapping: dict[int, str] = {}
        for symbol in (resp.get("payload") or {}).get("symbol", []) or []:
            try:
                sid = int(symbol.get("symbolId") or 0)
                name = str(symbol.get("symbolName") or sid or "")
                if sid:
                    mapping[sid] = name
            except (TypeError, ValueError):
                continue
        self._symbol_cache[int(account_id)] = mapping
        return mapping

    async def resolve_symbol_id(
        self, access_token: str, symbol: str, *, creds: CTraderCredentials, account_id: int
    ) -> int | None:
        """Map a human symbol (BTCUSD) to cTrader's numeric symbolId."""
        wanted = _normalize_symbol(symbol)
        account_id = int(account_id)
        cached = self._symbol_cache.get(account_id)
        if cached:
            for symbol_id, name in cached.items():
                if _normalize_symbol(name) == wanted:
                    return symbol_id
        async with self._session(creds, access_token=access_token, account_id=account_id) as ws:
            mapping = await self._symbol_map(ws, account_id)
        for symbol_id, name in mapping.items():
            if _normalize_symbol(name) == wanted:
                return symbol_id
        return None

    async def get_positions(
        self, access_token: str, *, creds: CTraderCredentials, account_id: int
    ) -> list[CTraderPosition]:
        """Open positions for an account (reconcile), resolving symbol names."""
        account_id = int(account_id)
        async with self._session(creds, access_token=access_token, account_id=account_id) as ws:
            resp = await self._request(
                ws,
                PT_RECONCILE_REQ,
                {"ctidTraderAccountId": account_id},
                expect={PT_RECONCILE_RES},
                timeout=self._timeout,
            )
            raw_positions = (resp.get("payload") or {}).get("position", []) or []
            symbols = await self._symbol_map(ws, account_id) if raw_positions else {}
            positions: list[CTraderPosition] = []
            for pos in raw_positions:
                trade_data = pos.get("tradeData") or {}
                symbol_id = int(trade_data.get("symbolId") or 0)
                side = int(trade_data.get("tradeSide") or 0)
                positions.append(
                    CTraderPosition(
                        position_id=str(pos.get("positionId") or ""),
                        symbol=symbols.get(symbol_id, str(symbol_id)),
                        trade_side="Buy" if side == TRADE_SIDE_BUY else "Sell",
                        volume=float(trade_data.get("volume") or 0) / 100.0,
                        price=float(pos.get("price") or 0.0),
                    )
                )
        return positions

    async def place_market_order(
        self,
        *,
        access_token: str,
        creds: CTraderCredentials,
        account_id: int,
        symbol_id: int,
        side: str,  # Buy / Sell
        volume: float,
        stop_loss: float,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> CTraderOrderReply:
        """Place a market order, then attach SL/TP after the fill (2110)."""
        account_id = int(account_id)
        client_order_id = client_order_id or f"cw-{uuid4().hex[:12]}"
        async with self._session(creds, access_token=access_token, account_id=account_id) as ws:
            filled = await self._request_execution(
                ws,
                PT_NEW_ORDER_REQ,
                {
                    "ctidTraderAccountId": account_id,
                    "symbolId": int(symbol_id),
                    "orderType": ORDER_TYPE_MARKET,
                    "tradeSide": TRADE_SIDE_BUY if side.upper() == "BUY" else TRADE_SIDE_SELL,
                    "volume": _volume_to_proto(volume),
                    "comment": "CryptoWatchman automated",
                    "clientOrderId": client_order_id,
                },
                ok_types=_ORDER_FILL_OK,
                fail_types=_ORDER_FILL_FAIL,
                client_order_id=client_order_id,
            )
            order_id = int(((filled.get("order") or {}).get("orderId")) or 0)
            position = filled.get("position") or {}
            position_id = int(position.get("positionId") or 0)

            if (stop_loss or take_profit) and position_id:
                sltp = {"positionId": position_id}
                if stop_loss:
                    sltp["stopLoss"] = round(float(stop_loss), 6)
                if take_profit:
                    sltp["takeProfit"] = round(float(take_profit), 6)
                try:
                    await self._request_execution(
                        ws,
                        PT_AMEND_POSITION_SLTP_REQ,
                        sltp,
                        ok_types=_AMEND_OK,
                        fail_types=_AMEND_FAIL,
                        client_order_id=client_order_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "cTrader SL/TP attach failed for position %s (%s); closing position",
                        position_id, exc,
                    )
                    try:
                        await self._request_execution(
                            ws,
                            PT_CLOSE_POSITION_REQ,
                            {
                                "ctidTraderAccountId": account_id,
                                "positionId": position_id,
                                "volume": _volume_to_proto(volume),
                            },
                            ok_types=_ORDER_FILL_OK,
                            fail_types=_ORDER_FILL_FAIL,
                            timeout=15.0,
                        )
                    except Exception as close_exc:  # noqa: BLE001
                        logger.error("cTrader close-after-amend-failure also failed: %s", close_exc)
                    raise CTraderError(
                        f"Order {order_id} filled but SL/TP attach failed ({exc}); "
                        "position auto-closed."
                    ) from exc

        return CTraderOrderReply(order_id=str(order_id) or client_order_id, status="Placed")

    async def close_position(
        self,
        access_token: str,
        *,
        creds: CTraderCredentials,
        account_id: int,
        position_id: str,
        volume: float | None = None,
    ) -> None:
        """Close a position by id (volume = full remaining size in units)."""
        account_id = int(account_id)
        if volume is None:
            volume = await self._position_volume(access_token, creds, account_id, position_id)
        if not volume:
            raise CTraderError(f"cTrader position {position_id} not found or already closed")
        async with self._session(creds, access_token=access_token, account_id=account_id) as ws:
            await self._request_execution(
                ws,
                PT_CLOSE_POSITION_REQ,
                {
                    "ctidTraderAccountId": account_id,
                    "positionId": int(position_id),
                    "volume": _volume_to_proto(volume),
                },
                ok_types=_ORDER_FILL_OK,
                fail_types=_ORDER_FILL_FAIL,
            )

    async def _position_volume(
        self, access_token: str, creds: CTraderCredentials, account_id: int, position_id: str
    ) -> float:
        for pos in await self.get_positions(access_token, creds=creds, account_id=account_id):
            if str(pos.position_id) == str(position_id):
                return pos.volume
        return 0.0