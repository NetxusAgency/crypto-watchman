"""Tests for the cTrader Open API (cTID) OAuth flow: authorize URL, code
exchange, token refresh, expiry handling, and the start/callback endpoints."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.services.broker import decrypt_secret, encrypt_secret
from app.services.trading import ctid_oauth as oauth


async def _fake_user(session=None, telegram_id=555, username=None):
    return SimpleNamespace(id=7, telegram_id=telegram_id, plan="pro", username=username or "tester")


async def _no_connections(session, user_id, **kw):
    return []


async def _nope(*a, **kw):
    return False


class _FakeSession:
    def __init__(self, franked=None):
        self.franked = franked

    async def flush(self):
        return None

    async def commit(self):
        return None


class TestAuthorizeUrl:
    def test_build_url_pieces(self, monkeypatch):
        monkeypatch.setattr(settings, "CTRADER_OAUTH_AUTHORIZE_URL", "https://id.ctrader.com/oauth/authorize")
        url = oauth.build_authorize_url(client_id="cid-123", state="st4t3")
        parsed = urlparse(url)
        assert parsed.netloc == "id.ctrader.com"
        qs = parse_qs(parsed.query)
        assert qs["client_id"] == ["cid-123"]
        assert qs["response_type"] == ["code"]
        assert qs["state"] == ["st4t3"]
        assert qs["redirect_uri"][0] == oauth.get_redirect_uri()

    def test_missing_client_id_rejected(self):
        with pytest.raises(oauth.CTraderOAuthError):
            oauth.build_authorize_url(client_id="", state="s")

    def test_redirect_derived_from_public_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "CTRADER_OAUTH_REDIRECT_URI", "")
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://crypto-watchman.onrender.com")
        assert (
            oauth.get_redirect_uri()
            == "https://crypto-watchman.onrender.com/api/trading/live/ctid/callback"
        )

    def test_redirect_explicit_wins(self, monkeypatch):
        monkeypatch.setattr(
            settings, "CTRADER_OAUTH_REDIRECT_URI", "https://example.com/cb"
        )
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://crypto-watchman.onrender.com")
        assert oauth.get_redirect_uri() == "https://example.com/cb"

    def test_redirect_localhost_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "CTRADER_OAUTH_REDIRECT_URI", "")
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
        assert oauth.get_redirect_uri() == "http://localhost:8000/api/trading/live/ctid/callback"

    def test_start_then_consume_roundtrip(self):
        url = oauth.start_authorization(
            telegram_id=555, client_id="cid-123", client_secret="cs-secret", mode="demo", label="My acc"
        )
        assert "client_id=cid-123" in url
        state = parse_qs(urlparse(url).query)["state"][0]
        pending = oauth.consume_pending_state(state)
        assert pending.telegram_id == 555
        assert pending.mode == "demo"
        assert decrypt_secret(pending.client_id_enc) == "cid-123"
        assert decrypt_secret(pending.client_secret_enc) == "cs-secret"

    def test_state_is_single_use(self):
        url = oauth.start_authorization(telegram_id=555, client_id="c", client_secret="s")
        state = parse_qs(urlparse(url).query)["state"][0]
        oauth.consume_pending_state(state)
        with pytest.raises(oauth.CTraderOAuthError):
            oauth.consume_pending_state(state)

    def test_expired_state_rejected(self, monkeypatch):
        monkeypatch.setattr(oauth, "STATE_TTL_SECONDS", 0)
        url = oauth.start_authorization(telegram_id=555, client_id="c", client_secret="s")
        state = parse_qs(urlparse(url).query)["state"][0]
        with pytest.raises(oauth.CTraderOAuthError):
            oauth.consume_pending_state(state)

    def test_missing_state_rejected(self):
        with pytest.raises(oauth.CTraderOAuthError):
            oauth.consume_pending_state("")


class TestExchangeAndRefresh:
    @pytest.fixture
    def token_url(self, monkeypatch):
        monkeypatch.setattr(
            settings, "CTRADER_OAUTH_TOKEN_URL", "https://id.ctrader.com/oauth/token"
        )
        yield

    async def _fake_post(self, payload):
        return {
            "access_token": "AT-123",
            "refresh_token": "RT-1",
            "expires_in": 3600,
        }

    def test_exchange_code(self, monkeypatch, token_url):
        monkeypatch.setattr(oauth, "_post_token", self._fake_post)
        body = asyncio.run(oauth.exchange_code(code="code1", client_id="c", client_secret="s"))
        assert body["access_token"] == "AT-123"

    def test_exchange_missing_token_raises(self, monkeypatch, token_url):
        async def bad(payload):
            return {"error": "invalid_grant"}

        monkeypatch.setattr(oauth, "_post_token", bad)
        with pytest.raises(oauth.CTraderOAuthError):
            asyncio.run(oauth.exchange_code(code="c", client_id="ci", client_secret="cs"))

    def test_refresh_access_token(self, monkeypatch, token_url):
        monkeypatch.setattr(oauth, "_post_token", self._fake_post)
        body = asyncio.run(
            oauth.refresh_access_token(refresh_token="RT-1", client_id="c", client_secret="s")
        )
        assert body["access_token"] == "AT-123"

    def test_refresh_missing_credentials(self):
        with pytest.raises(oauth.CTraderOAuthError):
            asyncio.run(
                oauth.refresh_access_token(refresh_token="", client_id="c", client_secret="s")
            )


class TestExpiry:
    def test_expires_in_to_utc_approx(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        ts = oauth.expires_in_to_utc(3600)
        after = datetime.now(timezone.utc).replace(tzinfo=None)
        assert before <= ts <= after + timedelta(seconds=3600)

    def test_token_is_expired_unknown_expiry(self):
        conn = SimpleNamespace(token_expires_at=None)
        assert oauth.token_is_expired(conn) is False

    def test_token_is_expired_in_past(self):
        conn = SimpleNamespace(token_expires_at=datetime.utcnow() - timedelta(minutes=5))
        assert oauth.token_is_expired(conn) is True

    def test_token_is_expired_in_future(self):
        conn = SimpleNamespace(token_expires_at=datetime.utcnow() + timedelta(hours=2))
        assert oauth.token_is_expired(conn) is False


def _connection(**kw):
    base = dict(
        id=1,
        account_id="100",
        access_token_enc=encrypt_secret("old-access"),
        refresh_token_enc=encrypt_secret("old-refresh"),
        client_id_enc=encrypt_secret("cid"),
        client_secret_enc=encrypt_secret("cs"),
        token_expires_at=datetime.utcnow() - timedelta(minutes=5),
        is_active=True,
        is_live=True,
        mode="demo",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestEnsureValidToken:
    def test_fresh_token_untouched(self):
        conn = _connection(token_expires_at=datetime.utcnow() + timedelta(hours=1))
        assert asyncio.run(oauth.ensure_valid_token(_FakeSession(), conn)) is True
        assert decrypt_secret(conn.access_token_enc) == "old-access"

    def test_expired_without_refresh_creds(self):
        conn = _connection(refresh_token_enc="", client_id_enc="")
        assert asyncio.run(oauth.ensure_valid_token(_FakeSession(), conn)) is False

    def test_expired_refreshes_and_updates(self, monkeypatch):
        async def fake_refresh(**kw):
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

        monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)
        conn = _connection()
        assert asyncio.run(oauth.ensure_valid_token(_FakeSession(), conn)) is True
        assert decrypt_secret(conn.access_token_enc) == "new-access"
        assert decrypt_secret(conn.refresh_token_enc) == "new-refresh"
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert now_utc < conn.token_expires_at <= now_utc + timedelta(hours=2)

    def test_expired_refresh_failure(self, monkeypatch):
        async def boom(**kw):
            raise oauth.CTraderOAuthError("nope")

        monkeypatch.setattr(oauth, "refresh_access_token", boom)
        conn = _connection()
        assert asyncio.run(oauth.ensure_valid_token(_FakeSession(), conn)) is False


def _client_with_auth(monkeypatch):
    import app.api.routes as routes
    from app.services import db_service as db

    monkeypatch.setattr(db, "get_or_create_user", _fake_user)
    monkeypatch.setattr(db, "get_user_broker_connections", _no_connections)
    monkeypatch.setattr(db, "get_user_live_trades", _no_connections)
    monkeypatch.setattr(db, "get_user_strategies", _no_connections)
    monkeypatch.setattr(settings, "ADMIN_TELEGRAM_ID", 555)
    monkeypatch.setattr(
        settings, "PUBLIC_BASE_URL", "http://localhost:8000"
    )

    from starlette.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    client.headers["X-App-Token"] = routes.security.issue_token(555)
    return client, routes, db


class TestOAuthEndpoints:
    def test_start_requires_client_credentials(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)
        resp = client.post("/api/trading/live/ctid/start", json={"client_id": "", "client_secret": ""})
        assert resp.status_code == 400

    def test_start_returns_authorize_url(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)
        resp = client.post(
            "/api/trading/live/ctid/start",
            json={"client_id": "cid-123", "client_secret": "cs-xyz", "mode": "live", "label": "LIVE"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["authorize_url"].startswith("https://")
        assert "client_id=cid-123" in body["authorize_url"]
        assert "cs-xyz" not in body["authorize_url"] and "cs-xyz" not in str(body)

    def test_callback_error_redirects(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)
        resp = client.get(
            "/api/trading/live/ctid/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "ctid=error" in resp.headers["location"]

    def test_callback_bad_state_redirects(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)
        resp = client.get(
            "/api/trading/live/ctid/callback",
            params={"code": "x", "state": "bogus"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "ctid=error" in resp.headers["location"]

    def test_callback_saves_connection_and_redirects(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)
        saved = {}

        async def fake_save(session, user_id, **kw):
            saved.update(kw)
            return SimpleNamespace(id=1, **kw)

        monkeypatch.setattr(db, "save_broker_connection", fake_save)

        async def fake_exchange(**kw):
            return {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600}

        monkeypatch.setattr(oauth, "exchange_code", fake_exchange)

        async def fake_accounts(self, access_token):
            return [SimpleNamespace(account_id=10012345)]

        from app.execution import ctrader

        monkeypatch.setattr(ctrader.CTraderClient, "get_accounts", fake_accounts)

        url = oauth.start_authorization(
            telegram_id=555, client_id="cid-123", client_secret="cs-secret", mode="live", label="LIVE acc"
        )
        state = parse_qs(urlparse(url).query)["state"][0]
        resp = client.get(
            "/api/trading/live/ctid/callback",
            params={"code": "granted", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "http://localhost:8000/app?ctid=ok"
        assert saved["account_id"] == "10012345"
        assert saved["mode"] == "live"
        assert saved["label"] == "LIVE acc"
        assert decrypt_secret(saved["access_token_enc"]) == "AT-1"
        assert decrypt_secret(saved["refresh_token_enc"]) == "RT-1"
        assert decrypt_secret(saved["client_id_enc"]) == "cid-123"

    def test_callback_exchange_failure_redirects(self, monkeypatch):
        client, routes, db = _client_with_auth(monkeypatch)

        async def boom(**kw):
            raise oauth.CTraderOAuthError("bad code")

        monkeypatch.setattr(oauth, "exchange_code", boom)

        url = oauth.start_authorization(telegram_id=555, client_id="c", client_secret="s")
        state = parse_qs(urlparse(url).query)["state"][0]
        resp = client.get(
            "/api/trading/live/ctid/callback",
            params={"code": "bad", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "ctid=error" in resp.headers["location"]