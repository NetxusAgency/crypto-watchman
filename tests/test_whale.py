import asyncio

from httpx import AsyncClient, MockTransport, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.models import Asset, WhalePreference, WhaleTransaction
from app.database.session import Base
from app.services.whale_tracker.assets import (
    DEFAULT_ASSETS, get_assets_map, get_user_scan_symbols, seed_assets, set_user_scan_symbols,
)
from app.services.whale_tracker.direction import classify_direction
from app.services.whale_tracker.providers import BitcoinWhaleProvider, Erc20WhaleProvider, WhaleTransfer
from app.services.whale_tracker.whale_tracker import WhaleTracker, dedup_key


class TestDedupKey:
    def test_deterministic(self):
        assert dedup_key("BTC", "abc123") == dedup_key("btc", "abc123")

    def test_distinct_txs(self):
        assert dedup_key("BTC", "tx1") != dedup_key("BTC", "tx2")


class TestDefaultAssets:
    def test_valid_registry(self):
        symbols = [a["symbol"] for a in DEFAULT_ASSETS]
        assert len(symbols) == len(set(symbols)), "duplicate symbols"
        for a in DEFAULT_ASSETS:
            assert a["whale_threshold"] > 0
            assert a["tier"] in {"high", "medium", "low"}
            if a["chain"] == "ethereum" and a["symbol"] != "ETH":
                assert a["contract_address"], f"{a['symbol']} missing contract address"
                assert 0 < a["decimals"] <= 18


class TestBitcoinProvider:
    def test_parses_block_txs(self):
        def handler(request):
            if "v1/blockchain" in str(request.url):
                return Response(200, json={"tipHashKey": "block1"})
            return Response(200, json=[
                {"txid": "tx_big", "vout": [{"value": 2_500_000_000}, {"value": 500_000_000}]},
                {"txid": "tx_small", "vout": [{"value": 1000}]},
            ])

        client = AsyncClient(transport=MockTransport(handler))
        provider = BitcoinWhaleProvider(client)

        async def run():
            whales = await provider.fetch_whales(10)
            return whales

        whales = asyncio.run(run())
        assert whales[0].asset == "BTC"
        assert whales[0].txid == "tx_big"
        assert whales[0].value == 30.0  # 3_000_000_000 sats
        assert whales[1].value == 0.00001

    def test_extracts_addresses_and_classifies(self):
        def handler(request):
            if "v1/blockchain" in str(request.url):
                return Response(200, json={"tipHashKey": "block1"})
            return Response(200, json=[
                {
                    "txid": "tx_whale",
                    "vin": [{"prevout": {"scriptpubkey_address": "1from"}}],
                    "vout": [{"value": 4_000_000_000, "scriptpubkey_address": "34xp4vrocgjym3xr7ycvpfhocnxv4twseo"}],
                },
            ])

        client = AsyncClient(transport=MockTransport(handler))
        provider = BitcoinWhaleProvider(client)

        async def run():
            return await provider.fetch_whales(10)

        whales = asyncio.run(run())
        assert whales[0].from_addr == "1from"
        assert whales[0].to_addr == "34xp4vrocgjym3xr7ycvpfhocnxv4twseo"
        assert whales[0].direction == "SELL"  # deposit to Binance hot wallet


class TestErc20Provider:
    def test_decimals_conversion(self, monkeypatch):
        monkeypatch.setattr("app.services.whale_tracker.providers.settings.ETHERSCAN_API_KEY", "test-key")

        def handler(request):
            return Response(200, json={
                "status": "1",
                "result": [
                    {"hash": "0x1", "value": "1500000000", "tokenDecimal": "6", "from": "0xa", "to": "0xb"},
                    {"hash": "0x2", "value": "500000000000000000", "tokenDecimal": "18", "from": "0xc", "to": "0xd"},
                ],
            })

        client = AsyncClient(transport=MockTransport(handler))
        token = Asset(symbol="USDT", chain="ethereum", contract_address="0xabc", decimals=6, whale_threshold=1_000_000)
        provider = Erc20WhaleProvider(client, [token], max_per_asset=50)

        async def run():
            return await provider.fetch_whales(10)

        whales = asyncio.run(run())
        assert whales[0].asset == "USDT"
        assert whales[0].value == 1500.0
        assert whales[0].txid == "0x1"
        assert whales[1].value == 0.5

    def test_classifies_dex_flows(self, monkeypatch):
        monkeypatch.setattr("app.services.whale_tracker.providers.settings.ETHERSCAN_API_KEY", "test-key")
        router = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"

        def handler(request):
            return Response(200, json={
                "status": "1",
                "result": [
                    {"hash": "0x1", "value": "1500000000000000000", "tokenDecimal": "18", "from": "0xa", "to": router},
                    {"hash": "0x2", "value": "1500000000000000000", "tokenDecimal": "18", "from": router, "to": "0xb"},
                ],
            })

        client = AsyncClient(transport=MockTransport(handler))
        token = Asset(symbol="LINK", chain="ethereum", contract_address="0xabc", decimals=18, whale_threshold=1)
        provider = Erc20WhaleProvider(client, [token], max_per_asset=50)

        async def run():
            return await provider.fetch_whales(10)

        whales = asyncio.run(run())
        assert whales[0].direction == "SELL"  # tokens -> router
        assert whales[1].direction == "BUY"   # tokens <- router


class TestDirectionClassifier:
    def test_dex_sell(self):
        direction, conf = classify_direction(
            "ethereum", "0xaaa", "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
        )
        assert direction == "SELL"
        assert conf == "high"

    def test_dex_buy(self):
        direction, _ = classify_direction(
            "ethereum", "0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "0xbbb"
        )
        assert direction == "BUY"

    def test_exchange_deposit(self):
        direction, conf = classify_direction("bitcoin", "wallet", "34xp4vrocgjym3xr7ycvpfhocnxv4twseo")
        assert direction == "SELL"
        assert conf == "medium"

    def test_unknown_transfer(self):
        direction, conf = classify_direction("bitcoin", "a", "b")
        assert direction == "TRANSFER"
        assert conf == "low"

    def test_case_insensitive(self):
        direction, _ = classify_direction("BTC", "0x777", "34XP4VROCGJYM3XR7YCVPFHOCNXV4TWSEO")
        assert direction == "SELL"


class TestWhalePreferences:
    def test_set_and_get(self):
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        async def flow():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                assert await get_user_scan_symbols(session, user_id=1) == []
                await set_user_scan_symbols(session, user_id=1, symbols=["btc", "ETH", "usdt"])
                assert await get_user_scan_symbols(session, user_id=1) == ["BTC", "ETH", "USDT"]
                await set_user_scan_symbols(session, user_id=1, symbols=[])
                assert await get_user_scan_symbols(session, user_id=1) == []
                rows = (await session.execute(select(WhalePreference))).scalars().all()
                assert rows == []

            await engine.dispose()

        asyncio.run(flow())


class _FakeProvider:
    def __init__(self, transfers):
        self.transfers = transfers

    async def fetch_whales(self, max_items):
        return self.transfers


class TestFetchAndStore:
    def test_dedup_and_usd(self, monkeypatch):
        def run():
            engine = create_async_engine(
                "sqlite+aiosqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )

            async def flow():
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with maker() as session:
                    await seed_assets(session)
                    assert "BTC" in await get_assets_map(session)

                    tracker = WhaleTracker()
                    fake = WhaleTransfer(
                        asset="BTC", chain="bitcoin", value=50.0, txid="tx_new",
                        to_addr="0xto", source="fake", raw={},
                    )

                    async def fake_providers(ses, symbols=None):
                        return [_FakeProvider([fake])]

                    async def fake_usd(symbol, amount):
                        return 61_000.0

                    monkeypatch.setattr(tracker, "_providers", fake_providers)
                    monkeypatch.setattr(tracker, "_usd_value", fake_usd)
                    monkeypatch.setattr(tracker, "_last_scan", 0)

                    new_txs = await tracker.fetch_and_store(session)
                    assert len(new_txs) == 1
                    assert new_txs[0]["asset"] == "BTC"
                    assert new_txs[0]["value_usd"] == 61_000.0

                    new_txs2 = await tracker.fetch_and_store(session)
                    assert new_txs2 == []

                    rows = (await session.execute(select(WhaleTransaction))).scalars().all()
                    assert len(rows) == 1
                    assert rows[0].dedup_key == dedup_key("BTC", "tx_new")

                await engine.dispose()

            asyncio.run(flow())

        run()

    def test_below_threshold_not_stored(self, monkeypatch):
        def run():
            engine = create_async_engine(
                "sqlite+aiosqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )

            async def flow():
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with maker() as session:
                    await seed_assets(session)
                    tracker = WhaleTracker()
                    small = WhaleTransfer(asset="BTC", chain="bitcoin", value=1.0, txid="tx_small")

                    async def fake_providers(ses, symbols=None):
                        return [_FakeProvider([small])]

                    monkeypatch.setattr(tracker, "_providers", fake_providers)
                    monkeypatch.setattr(tracker, "_last_scan", 0)
                    monkeypatch.setattr(tracker, "_usd_value", lambda s, a: None)

                    new_txs = await tracker.fetch_and_store(session)
                    assert new_txs == []  # 1 BTC < 10 BTC threshold

                await engine.dispose()

            asyncio.run(flow())

        run()

    def test_stores_direction_and_respects_symbols(self, monkeypatch):
        def run():
            engine = create_async_engine(
                "sqlite+aiosqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )

            async def flow():
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with maker() as session:
                    await seed_assets(session)
                    tracker = WhaleTracker()
                    called_with = {}

                    whale = WhaleTransfer(
                        asset="BTC", chain="bitcoin", value=50.0, txid="tx_direction",
                        from_addr="wallet",
                        to_addr="34xp4vrocgjym3xr7ycvpfhocnxv4twseo",
                        source="fake", direction="SELL", direction_confidence="medium", raw={},
                    )

                    async def fake_providers(ses, symbols=None):
                        called_with["symbols"] = symbols
                        return [_FakeProvider([whale])]

                    async def fake_usd(symbol, amount):
                        return 2_000_000.0

                    monkeypatch.setattr(tracker, "_providers", fake_providers)
                    monkeypatch.setattr(tracker, "_usd_value", fake_usd)
                    monkeypatch.setattr(tracker, "_last_scan", 0)

                    new_txs = await tracker.fetch_and_store(session, symbols={"BTC"})
                    assert called_with["symbols"] == {"BTC"}
                    assert new_txs[0]["direction"] == "SELL"

                    rows = (await session.execute(select(WhaleTransaction))).scalars().all()
                    assert rows[0].direction == "SELL"
                    assert rows[0].direction_confidence == "medium"

                await engine.dispose()

            asyncio.run(flow())

        run()