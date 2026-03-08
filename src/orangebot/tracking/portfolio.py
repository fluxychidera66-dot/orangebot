"""Portfolio and balance tracking for OrangeBot."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass
class BalanceSnapshot:
    """A point-in-time balance snapshot."""
    timestamp: str
    polymarket_usdc: float
    total_usd: float
    positions_value: float = 0.0
    kalshi_usd: float = 0.0


class PortfolioTracker:
    """Fetches and records wallet balances."""

    def __init__(self) -> None:
        self._settings = get_settings()

    async def get_current_balances(self) -> dict[str, Any]:
        """Fetch current USDC balance from Polygon."""
        settings = self._settings
        timestamp = datetime.now(timezone.utc).isoformat()

        if not settings.wallet_address:
            return {
                "polymarket_usdc": 0.0,
                "kalshi_usd": 0.0,
                "total_usd": 0.0,
                "timestamp": timestamp,
            }

        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
            usdc = w3.eth.contract(
                address=Web3.to_checksum_address(USDC_CONTRACT),
                abi=USDC_ABI,
            )
            wallet = Web3.to_checksum_address(settings.wallet_address)
            raw_balance = usdc.functions.balanceOf(wallet).call()
            usdc_balance = raw_balance / 1_000_000  # USDC has 6 decimals

            return {
                "polymarket_usdc": usdc_balance,
                "kalshi_usd": 0.0,
                "total_usd": usdc_balance,
                "timestamp": timestamp,
            }
        except Exception as e:
            log.error("Failed to fetch balance", error=str(e))
            return {
                "polymarket_usdc": 0.0,
                "kalshi_usd": 0.0,
                "total_usd": 0.0,
                "timestamp": timestamp,
            }

    def record_snapshot(self, snapshot: BalanceSnapshot) -> None:
        """Synchronously fire-and-forget snapshot recording."""
        asyncio.create_task(self.record_snapshot_async(snapshot))

    async def record_snapshot_async(self, snapshot: BalanceSnapshot) -> None:
        """Async snapshot recording to SQLite."""
        from orangebot.data.repositories import PortfolioRepository
        try:
            await PortfolioRepository.insert(
                timestamp=snapshot.timestamp,
                polymarket_usdc=snapshot.polymarket_usdc,
                total_usd=snapshot.total_usd,
                positions_value=snapshot.positions_value,
            )
        except Exception as e:
            log.debug("Failed to record snapshot", error=str(e))

    async def get_snapshot_history(self, limit: int = 100) -> list[dict]:
        """Get recent balance history from DB."""
        from orangebot.data.repositories import PortfolioRepository
        return await PortfolioRepository.get_recent(limit=limit)
