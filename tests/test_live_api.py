"""Tests for the live-execution wiring (broker connections + propose/confirm flow)."""

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.broker import decrypt_secret, encrypt_secret, mask_secret


class TestBrokerEncryption:
    def test_roundtrip(self):
        token = "super-secret-cTrader-access-token"
        enc = encrypt_secret(token)
        assert enc != token
        assert decrypt_secret(enc) == token

    def test_mask_hides_secret(self):
        masked = mask_secret(encrypt_secret("mysecret123"))
        assert "mysecret123" not in masked
        assert len(masked) > 0

    def test_mask_handles_blank(self):
        assert mask_secret("") == ""


def _fake_connection(account_id="10012345", is_live=False, is_active=True, mode="demo"):
    return SimpleNamespace(
        id=1,
        user_id=7,
        label="My cTrader",
        platform="ctrader",
        account_id=account_id,
        is_live=is_live,
        is_active=is_active,
        mode=mode,
        access_token_enc=encrypt_secret("tok"),
        client_id_enc="",
        client_secret_enc="",
        created_at=None,
    )


async def _fake_user(session=None, telegram_id=555, username=None):
    return SimpleNamespace(id=7, telegram_id=telegram_id, plan="pro", username=username or "tester")


async def _no_connections(session, user_id):
    return []


async def _no_trades(session, user_id, **kw):
    return []


async def _no_strategies(session, user_id, **kw):
    return []


async def _nope(*a, **kw):
    return False


async def _one_armed(session, user_id):
    return [_fake_connection(is_live=True)]


async def _one_active(session, user_id):
    return [_fake_connection(is_live=False, is_active=True)]


@pytest.fixture
def live_client(monkeypatch):
    import app.api.routes as routes
    from app.services import db_service as db

    monkeypatch.setattr(db, "get_or_create_user", _fake_user)
    monkeypatch.setattr(db, "get_user_broker_connections", _no_connections)
    monkeypatch.setattr(db, "get_user_live_trades", _no_trades)
    monkeypatch.setattr(db, "get_user_strategies", _no_strategies)

    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", 555)
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)
    token = routes.security.issue_token(555)

    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    client.headers["X-App-Token"] = token
    return client, routes, db


class TestConnectionsAuth:
    def test_requires_app_token(self):
        from starlette.testclient import TestClient
        from app.main import app

        resp = TestClient(app).get("/api/trading/live")
        assert resp.status_code == 401

    def test_live_overview_reads(self, live_client):
        client, routes, db = live_client
        resp = client.get("/api/trading/live")
        assert resp.status_code == 200
        assert resp.json()["live_enabled_global"] is False


class TestConnections:
    def test_save_connection_masks_secret(self, live_client, monkeypatch):
        client, routes, db = live_client

        async def fake_save(session, user_id, **kw):
            return SimpleNamespace(
                id=1,
                label=kw.get("label", ""),
                platform=kw.get("platform", "ctrader"),
                account_id=kw.get("account_id", ""),
                is_live=kw.get("is_live", False),
                is_active=True,
                mode=kw.get("mode", "demo"),
                access_token_enc=kw.get("access_token_enc", "enc:abc"),
                client_id_enc=kw.get("client_id_enc", ""),
                client_secret_enc=kw.get("client_secret_enc", ""),
                created_at=None,
            )

        monkeypatch.setattr(db, "save_broker_connection", fake_save)

        resp = client.post(
            "/api/trading/live/connections",
            json={
                "label": "My cTrader",
                "account_id": "10012345",
                "access_token": "real-secret-token",
                "client_id": "",
                "client_secret": "",
                "mode": "live",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["connection"]
        assert data["account_id"] == "10012345"
        assert data["mode"] == "live"
        assert "real-secret-token" not in str(data)
        assert "masked" in str(data)

    def test_arm_requires_existing(self, live_client, monkeypatch):
        client, routes, db = live_client
        monkeypatch.setattr(db, "set_broker_connection_live", _nope)
        resp = client.post("/api/trading/live/connections/99/arm", json={"is_live": True})
        assert resp.status_code == 404

    def test_delete_requires_existing(self, live_client, monkeypatch):
        client, routes, db = live_client
        monkeypatch.setattr(db, "delete_broker_connection", _nope)
        resp = client.delete("/api/trading/live/connections/99")
        assert resp.status_code == 404


class TestExecutePipeline:
    def test_dry_run_requires_a_connection(self, live_client):
        client, routes, db = live_client
        resp = client.post(
            "/api/trading/live/execute",
            json={"symbol": "BTCUSD", "strategy_key": "momentum", "dry_run": True},
        )
        assert resp.status_code == 400
        assert "connection" in resp.json()["detail"].lower()

    def test_demo_armed_proceeds_without_global_switch(self, live_client, monkeypatch):
        """A demo account only needs to be armed — not the global kill-switch."""
        client, routes, db = live_client
        monkeypatch.setattr(db, "get_user_broker_connections", _one_armed)
        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)

        class FakeResult:
            def __init__(self):
                self.allowed = True
                self.reason = "Proposal awaiting confirmation."
                self.trade = None
                self.plan_dict = None
                self.risk_dict = None
                self.awaiting_confirmation = True
                self.confirmation_text = ""

        class FakeService:
            async def propose_trade(self, session, **kw):
                assert kw["dry_run"] is False
                return FakeResult()

        monkeypatch.setattr(
            "app.services.trading.live.LiveExecutionService",
            lambda **defaults: FakeService(),
        )

        resp = client.post(
            "/api/trading/live/execute",
            json={"symbol": "BTCUSD", "strategy_key": "momentum", "dry_run": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is True
        assert body["awaiting_confirmation"] is True

    def test_live_mode_rejected_while_global_disabled(self, live_client, monkeypatch):
        """A real-money (live) account is blocked while LIVE_TRADING_ENABLED=false."""
        client, routes, db = live_client

        async def _one_armed_live(session, user_id):
            return [_fake_connection(is_live=True, mode="live")]

        monkeypatch.setattr(db, "get_user_broker_connections", _one_armed_live)
        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)

        class FakeResult:
            def __init__(self):
                self.allowed = False
                self.reason = "Live trading is disabled globally (LIVE_TRADING_ENABLED=false). Demo accounts may still trade; real-money accounts are blocked."
                self.trade = None
                self.plan_dict = None
                self.risk_dict = None
                self.awaiting_confirmation = False
                self.confirmation_text = ""

        class FakeService:
            async def propose_trade(self, session, **kw):
                return FakeResult()

        monkeypatch.setattr(
            "app.services.trading.live.LiveExecutionService",
            lambda **defaults: FakeService(),
        )

        resp = client.post(
            "/api/trading/live/execute",
            json={"symbol": "BTCUSD", "strategy_key": "momentum", "dry_run": False},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is False
        assert "disabled globally" in resp.json()["reason"]

    def test_dry_run_runs_pipeline_without_broker(self, live_client, monkeypatch):
        client, routes, db = live_client
        monkeypatch.setattr(db, "get_user_broker_connections", _one_active)

        class FakeResult:
            def __init__(self):
                self.allowed = False
                self.reason = "Mock evaluation: no setup confirmed right now."
                self.trade = None
                self.plan_dict = None
                self.risk_dict = None
                self.awaiting_confirmation = False
                self.confirmation_text = ""

        class FakeService:
            async def propose_trade(self, session, **kw):
                assert kw["dry_run"] is True
                return FakeResult()

        monkeypatch.setattr(
            "app.services.trading.live.LiveExecutionService",
            lambda **defaults: FakeService(),
        )

        resp = client.post(
            "/api/trading/live/execute",
            json={"symbol": "BTCUSD", "strategy_key": "momentum", "dry_run": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is False
        assert body["dry_run"] is True


class TestLiveEnabledGuard:
    def test_live_mode_needs_global_switch(self, monkeypatch):
        from app.services.trading.live import live_enabled

        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)
        conn = _fake_connection(is_live=True, is_active=True, mode="live")
        result = live_enabled(conn, require_arm=True)
        assert result.allowed is False
        assert "disabled globally" in result.reason

    def test_demo_mode_skips_global_switch(self, monkeypatch):
        from app.services.trading.live import live_enabled

        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", False)
        conn = _fake_connection(is_live=True, is_active=True, mode="demo")
        result = live_enabled(conn, require_arm=True)
        assert result.allowed is True

    def test_unarmed_rejected(self, monkeypatch):
        from app.services.trading.live import live_enabled

        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", True)
        conn = _fake_connection(is_live=False, is_active=True, mode="live")
        result = live_enabled(conn, require_arm=True)
        assert result.allowed is False
        assert "not armed" in result.reason

    def test_inactive_rejected(self, monkeypatch):
        from app.services.trading.live import live_enabled

        monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", True)
        conn = _fake_connection(is_live=True, is_active=False, mode="live")
        result = live_enabled(conn, require_arm=True)
        assert result.allowed is False
        assert "No active broker connection" in result.reason