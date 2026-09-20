import hashlib
import hmac
import time as real_time
import urllib.parse
from types import SimpleNamespace

from app.api import security
from app.api.routes import _serialize_alert, _serialize_wallet
from app.bot.keyboards import mini_app_url
from app.core.config import settings

BOT_TOKEN = "123456789:TESTTOKENAbCdEf"
VALID_USER_ID = 424242


def _build_init_data(bot_token=BOT_TOKEN, user_id=VALID_USER_ID, username="tester"):
    """Build a Telegram WebApp initData string with a valid signature."""
    params = {
        "query_id": "AAF123",
        "user": f'{{"id":{user_id},"first_name":"Test","username":"{username}"}}',
        "auth_date": str(int(real_time.time())),
        "hash": "",
    }
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k != "hash"
    )
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


class TestInitData:
    def test_valid_init_data(self):
        data = security.validate_init_data(_build_init_data(), BOT_TOKEN)
        assert data is not None
        assert data["user"]["id"] == VALID_USER_ID
        assert data["user"]["username"] == "tester"

    def test_tampered_user_rejected(self):
        init_data = _build_init_data()
        params = dict(urllib.parse.parse_qsl(init_data))
        params["user"] = '{"id":999999,"first_name":"Hacker","username":"hacker"}'
        tampered = urllib.parse.urlencode(params)
        assert security.validate_init_data(tampered, BOT_TOKEN) is None

    def test_placeholder_token_rejected(self):
        assert security.validate_init_data(_build_init_data(), "placeholder_token") is None

    def test_missing_hash_rejected(self):
        init_data = _build_init_data()
        stripped = urllib.parse.urlencode(
            {k: v for k, v in urllib.parse.parse_qsl(init_data) if k != "hash"}
        )
        assert security.validate_init_data(stripped, BOT_TOKEN) is None

    def test_expired_auth_date_rejected(self):
        init_data = _build_init_data()
        expired = init_data.replace(
            f"auth_date={int(real_time.time())}",
            f"auth_date={int(real_time.time()) - 3 * 86400}",
        )
        assert security.validate_init_data(expired, BOT_TOKEN) is None

    def test_empty_rejected(self):
        assert security.validate_init_data("", BOT_TOKEN) is None
        assert security.validate_init_data(None, BOT_TOKEN) is None

    def test_wrong_bot_token_rejected(self):
        assert (
            security.validate_init_data(_build_init_data(), "999:WRONGTOKEN") is None
        )


def _build_widget(bot_token=BOT_TOKEN, user_id=VALID_USER_ID, username="tester"):
    """Build a Telegram Login Widget auth payload (flat fields) with a valid signature."""
    fields = {
        "id": str(user_id),
        "first_name": "Test",
        "username": username,
        "auth_date": str(int(real_time.time())),
    }
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items())
    )
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return fields


class TestLoginWidget:
    def test_valid_widget(self):
        out = security.validate_widget_fields(_build_widget(), BOT_TOKEN)
        assert out is not None
        assert out["id"] == VALID_USER_ID
        assert out["username"] == "tester"

    def test_tampered_rejected(self):
        fields = _build_widget()
        fields["username"] = "evil"
        assert security.validate_widget_fields(fields, BOT_TOKEN) is None

    def test_expired_rejected(self):
        fields = _build_widget()
        fields["auth_date"] = str(int(real_time.time()) - 3 * 86400)
        assert security.validate_widget_fields(fields, BOT_TOKEN) is None

    def test_missing_id_rejected(self):
        fields = _build_widget()
        del fields["id"]
        assert security.validate_widget_fields(fields, BOT_TOKEN) is None

    def test_none_rejected(self):
        assert security.validate_widget_fields(None, BOT_TOKEN) is None
        assert security.validate_widget_fields({}, BOT_TOKEN) is None


class TestDevToken:
    def test_valid_accepts(self, monkeypatch):
        monkeypatch.setattr(settings, "MINI_APP_DEV_TOKEN", "supersecret")
        assert security.is_valid_dev_token("supersecret") is True

    def test_wrong_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "MINI_APP_DEV_TOKEN", "supersecret")
        assert security.is_valid_dev_token("wrong") is False

    def test_disabled_when_unset(self, monkeypatch):
        monkeypatch.setattr(settings, "MINI_APP_DEV_TOKEN", "")
        assert security.is_valid_dev_token("anything") is False
        assert security.is_valid_dev_token(None) is False


class TestAppToken:
    def test_roundtrip(self):
        token = security.issue_token(VALID_USER_ID)
        assert security.parse_token(token) == VALID_USER_ID

    def test_tampered_rejected(self):
        token = security.issue_token(VALID_USER_ID)
        mangled = ("A" if token[0] != "A" else "B") + token[1:]
        assert security.parse_token(mangled) is None

    def test_garbage_rejected(self):
        assert security.parse_token("not-a-token") is None
        assert security.parse_token(None) is None
        assert security.parse_token("") is None

    def test_expired_rejected(self, monkeypatch):
        class FakeClock:
            def __init__(self):
                self.now = 1_000_000_000

            def time(self):
                return self.now

        clock = FakeClock()
        monkeypatch.setattr(security.time, "time", clock.time)
        token = security.issue_token(VALID_USER_ID)
        clock.now += (settings.MINI_APP_TOKEN_TTL_HOURS * 3600) + 60
        assert security.parse_token(token) is None


class TestSerializers:
    def test_wallet_serializer(self):
        wallet = SimpleNamespace(
            id=1,
            network="ethereum",
            label="Main",
            address="0x2B17bbF42f9dfc9c32c015e2B0d896acB53C387e",
            total_value_usd=1234.567,
            balances=[
                SimpleNamespace(symbol="ETH", quantity=1000.0, value_usd=2000.0),
                SimpleNamespace(symbol="SHIB", quantity=0.0001, value_usd=0.0),
            ],
        )
        out = _serialize_wallet(wallet)
        assert out["network"] == "ethereum"
        assert out["total_value_usd"] == 1234.57
        assert out["balances"][0]["symbol"] == "ETH"  # sorted by value desc
        assert out["address_short"].startswith("0x2B17")

    def test_alert_serializer(self):
        alert = SimpleNamespace(
            id=7,
            symbol="BTC",
            alert_type="price_above",
            target_value=70000,
            created_at=None,
        )
        out = _serialize_alert(alert)
        assert out["description"] == "Price above 70,000.0000"
        assert out["alert_type"] == "price_above"


class TestMiniAppPage:
    def test_index_renders_widget_with_username(self, monkeypatch):
        monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "@testbot")
        from starlette.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get("/app")
        assert resp.status_code == 200
        assert "Sign in with Telegram" in resp.text
        assert 'data-telegram-login="@testbot"' in resp.text
        assert "__BOT_USERNAME__" not in resp.text

    def test_index_empty_username_replaced(self, monkeypatch):
        monkeypatch.setattr(settings, "TELEGRAM_BOT_USERNAME", "")
        from starlette.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get("/app/")
        assert resp.status_code == 200
        assert "__BOT_USERNAME__" not in resp.text

    def test_static_assets_served(self):
        from starlette.testclient import TestClient
        from app.main import app
        for path in ("/app/app.js", "/app/styles.css"):
            resp = TestClient(app).get(path)
            assert resp.status_code == 200


class TestMiniAppUrl:
    def test_https_url_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://watchman.example.com")
        assert mini_app_url() == "https://watchman.example.com/app/"

    def test_localhost_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
        assert mini_app_url() == "http://localhost:8000/app/"

    def test_insecure_remote_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://watchman.example.com")
        assert mini_app_url() is None