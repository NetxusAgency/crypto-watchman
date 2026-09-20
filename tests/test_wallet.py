import asyncio

from httpx import AsyncClient, MockTransport, Response
from types import SimpleNamespace

from app.services.wallet.providers import (
    normalize_network,
    parse_covalent_items,
    is_valid_address,
    PublicRpcProvider,
)
from app.services.wallet.wallet_service import (
    format_wallet_summary,
    shorten_address,
)

VALID = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # vitalik.eth mainnet
VALID_LOWER = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"


class TestNormalizeNetwork:
    def test_aliases(self):
        assert normalize_network("eth") == "ethereum"
        assert normalize_network("ETH") == "ethereum"
        assert normalize_network("matic") == "polygon"
        assert normalize_network("bnb") == "bsc"
        assert normalize_network("arb") == "arbitrum"
        assert normalize_network("op") == "optimism"
        assert normalize_network("avax") == "avalanche"
        assert normalize_network(None) == "ethereum"

    def test_unsupported_raises(self):
        try:
            normalize_network("solana")
            assert False, "should raise"
        except ValueError:
            pass


class TestAddressValidation:
    def test_valid(self):
        assert is_valid_address(VALID)
        assert is_valid_address(VALID_LOWER)

    def test_invalid(self):
        assert not is_valid_address("not-an-address")
        assert not is_valid_address("0x1234")
        assert not is_valid_address("0x" + "ab" * 40)  # 41 bytes


class TestParseCovalent:
    def test_native_and_token(self):
        items = [
            {
                "contract_ticker_symbol": "ETH",
                "contract_name": "Ethereum",
                "contract_address": "0x0000000000000000000000000000000000000000",
                "contract_decimals": 18,
                "balance": "2500000000000000000",
                "quote_rate": 3500.0,
                "quote": 8750.0,
            },
            {
                "contract_ticker_symbol": "USDC",
                "contract_name": "USD Coin",
                "contract_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "contract_decimals": 6,
                "balance": "1000000000",
                "quote_rate": 1.0,
                "quote": 1000.0,
            },
        ]
        balances = parse_covalent_items(items)
        eth = balances[0]
        usdc = balances[1]
        assert eth.symbol == "ETH"
        assert eth.contract_address == "native"
        assert eth.quantity == 2.5
        assert eth.value_usd == 8750.0
        assert usdc.symbol == "USDC"
        assert usdc.quantity == 1000.0
        assert usdc.value_usd == 1000.0

    def test_zero_balance_skipped(self):
        items = [
            {
                "contract_ticker_symbol": "ETH",
                "contract_name": "Ethereum",
                "contract_decimals": 18,
                "balance": "0",
            }
        ]
        assert parse_covalent_items(items) == []

    def test_empty(self):
        assert parse_covalent_items([]) == []
        assert parse_covalent_items(None) == []


class TestSummaryFormatting:
    def _wallet(self, address="0xd8da6bf26964af9d7eed9e03e53415d37aa96045", balances=None):
        return SimpleNamespace(
            address=address,
            network="ethereum",
            label=None,
            total_value_usd=9750.0,
            balances=[
                SimpleNamespace(symbol="ETH", quantity=2.5, value_usd=8750.0),
                SimpleNamespace(symbol="USDC", quantity=1000.0, value_usd=1000.0),
            ] if balances is None else balances,
        )

    def test_shorten_address(self):
        assert shorten_address(VALID) == "0xd8dA…6045"
        assert shorten_address("0x1234") == "0x1234"

    def test_summary_contains_total(self):
        lines = format_wallet_summary([self._wallet()])
        text = "\n".join(lines)
        assert "Wallet Overview" in text
        assert "$9,750.00" in text
        assert "ETH" in text
        assert "USDC" in text

    def test_summary_empty(self):
        lines = format_wallet_summary([])
        assert "No wallets connected yet." in "\n".join(lines)


def _rpc_provider(balance_hex: str | None, rpc_error: str | None = None):
    """PublicRpcProvider wired to a mocked RPC + CoinGecko."""
    def handler(request):
        url = str(request.url)
        if "cloudflare-eth.com" in url:
            if rpc_error:
                return Response(200, json={"jsonrpc": "2.0", "error": {"code": -32603, "message": rpc_error}, "id": 1})
            return Response(200, json={"jsonrpc": "2.0", "result": balance_hex, "id": 1})
        if "coingecko.com" in url:
            return Response(200, json={"ethereum": {"usd": 2607.4868}})
        return Response(404)

    transport = MockTransport(handler)
    return PublicRpcProvider(client=AsyncClient(transport=transport))


class TestPublicRpcProvider:
    def test_zero_balance_returns_eth_row(self):
        async def run():
            provider = _rpc_provider("0x0")
            balances = await provider.fetch_balances(VALID, "ethereum")
            await provider.close()
            return balances

        balances = asyncio.run(run())
        assert len(balances) == 1
        assert balances[0].symbol == "ETH"
        assert balances[0].quantity == 0.0
        assert balances[0].value_usd == 0.0

    def test_balance_with_price(self):
        async def run():
            provider = _rpc_provider("0xde0b6b3a7640000")  # 1 ETH
            balances = await provider.fetch_balances(VALID, "ethereum")
            await provider.close()
            return balances

        balances = asyncio.run(run())
        assert balances[0].quantity == 1.0
        assert balances[0].value_usd == 2607.49

    def test_jsonrpc_error_surfaces_real_message(self):
        async def run():
            provider = _rpc_provider(None, rpc_error="Internal error")
            try:
                await provider.fetch_balances(VALID, "ethereum")
                assert False, "should raise"
            except RuntimeError as e:
                return str(e)

        err = asyncio.run(run())
        assert "Internal error" in err

    def test_non_ethereum_rejected(self):
        async def run():
            provider = _rpc_provider("0x0")
            try:
                await provider.fetch_balances(VALID, "polygon")
                assert False, "should raise"
            except RuntimeError as e:
                return str(e)

        err = asyncio.run(run())
        assert "COVALENT_API_KEY" in err