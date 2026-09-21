"""cTrader Connect Open API v2 adapter.

Thin, testable HTTP client for the cTrader REST API. Lives behind the same
`ExecutionGateway` contract as the paper gateway, but is a real broker client:
it must ONLY ever be reached after `LiveExecutionService` passes its guard.

Reference endpoints (v2):
    GET  /getAccounts   -> list accounts w/ balance & equity for this token
    GET  /getSymbols    -> list symbols (numeric symbolId, "BTCUSD", ...)
    GET  /getPositions  -> open positions for the token
    POST /newOrder      -> place limit/stop/market order w/ optional SL & TP
    POST /closePosition -> close a position by positionId

Placement side uses the v2 `tradeSide` vocabulary ("Buy"/"Sell") and
`orderType` "Market"; responses carry `orderId`.
"""

from dataclasses import dataclass, field

import httpx

from app.core.config import settings

__all__ = ["CTraderAccount", "CTraderPosition", "CTraderClient", "CTraderError"]


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
class CTraderPayload:
    """What a client sent to the broker — captured on dry runs for tests/audit."""

    url: str
    headers: dict = field(default_factory=dict)
    json: dict | None = None


class CTraderClient:
    def __init__(self, base_url: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = (base_url or settings.CTRADER_API_BASE_URL).rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=12.0,
            headers={"User-Agent": "crypto-watchman/1.0"},
        )
        # Last payload sent; tests inspect this for dry-run assertions.
        self.last_payload: CTraderPayload | None = None

    async def close(self):
        if self._owns_client:
            await self.client.aclose()

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    async def _get(self, path: str, access_token: str) -> dict:
        resp = await self.client.get(
            f"{self.base_url}{path}", headers=self._headers(access_token)
        )
        self.last_payload = CTraderPayload(url=str(resp.url))
        if resp.status_code >= 400:
            raise CTraderError(f"cTrader {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def _post(self, path: str, access_token: str, payload: dict) -> dict:
        headers = self._headers(access_token)
        headers["Content-Type"] = "application/json"
        resp = await self.client.post(f"{self.base_url}{path}", headers=headers, json=payload)
        self.last_payload = CTraderPayload(url=str(resp.url), headers=headers, json=payload)
        if resp.status_code >= 400:
            raise CTraderError(f"cTrader {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def get_accounts(self, access_token: str) -> list[CTraderAccount]:
        data = await self._get("/getAccounts", access_token)
        accounts: list[CTraderAccount] = []
        for acc in data.get("accounts", []):
            accounts.append(CTraderAccount(
                account_id=int(acc.get("accountId", acc.get("id", 0))),
                balance=float(acc.get("balance", 0.0)),
                equity=float(acc.get("equity", acc.get("balance", 0.0))),
                currency=str(acc.get("currency", "USD") or "USD"),
            ))
        return accounts

    async def resolve_symbol_id(self, access_token: str, symbol: str) -> int | None:
        """Map a human symbol (BTCUSD) to cTrader's numeric symbolId."""
        wanted = symbol.upper().replace("/", "").replace("-", "")
        data = await self._get("/getSymbols", access_token)
        for s in data.get("symbols", []):
            if str(s.get("symbol", "")).upper().replace("/", "").replace("-", "") == wanted:
                return int(s.get("symbolId", 0))
        return None

    async def get_positions(self, access_token: str) -> list[CTraderPosition]:
        data = await self._get("/getPositions", access_token)
        out: list[CTraderPosition] = []
        for pos in data.get("positions", []):
            out.append(CTraderPosition(
                position_id=str(pos.get("positionId", "")),
                symbol=str(pos.get("symbol", "")),
                trade_side=str(pos.get("tradeSide", pos.get("side", ""))),
                volume=float(pos.get("volume", 0.0)),
                price=float(pos.get("price", 0.0)),
            ))
        return out

    async def place_market_order(
        self,
        *,
        access_token: str,
        account_id: int | str,
        symbol_id: int,
        side: str,  # Buy / Sell
        volume: float,
        stop_loss: float,
        take_profit: float | None = None,
    ) -> CTraderOrderReply:
        payload: dict = {
            "access_token": access_token,
            "accountId": int(account_id),
            "symbolId": int(symbol_id),
            "tradeSide": "Buy" if side.upper() == "BUY" else "Sell",
            "orderType": "Market",
            "volume": round(float(volume), 6),
            "stopLoss": round(float(stop_loss), 6),
        }
        if take_profit is not None:
            payload["takeProfit"] = round(float(take_profit), 6)
        data = await self._post("/newOrder", access_token, payload)
        order_id = str(data.get("orderId") or data.get("order_id") or "")
        return CTraderOrderReply(order_id=order_id, status=str(data.get("status", "Placed")))

    async def close_position(self, access_token: str, position_id: str) -> None:
        await self._post(
            "/closePosition", access_token,
            {"access_token": access_token, "positionId": str(position_id)},
        )