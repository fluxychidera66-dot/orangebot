"""
OrangeBot Fee Collector
=======================
Transparently collects a 1% developer fee on each trade executed by OrangeBot.

This fee supports free distribution of OrangeBot to all users.
The fee wallet address and percentage are openly disclosed in:
  - This source code
  - The README
  - The .env.example file
  - The dashboard UI

Fee flow:
  1. User executes a trade of size X USDC
  2. Fee = X * 0.01 (1%)
  3. Fee is transferred from user's wallet to OrangeBot developer wallet
     via a direct USDC transfer on Polygon network
  4. A fee log entry is recorded locally for transparency

Developer wallet: 0x8446df20DAae168bBFfBb6774d1a3D1D9b17f970
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

# USDC contract address on Polygon
USDC_CONTRACT_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Minimal ERC-20 ABI for USDC transfer
USDC_ABI = [
    {
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class FeeRecord:
    """A record of a single fee collection event."""
    timestamp: str
    trade_size_usd: float
    fee_usd: float
    fee_pct: float
    market: str
    tx_hash: Optional[str]
    status: str  # "sent", "skipped_dry_run", "failed"


class FeeCollector:
    """
    Handles transparent 1% developer fee collection for OrangeBot.

    Called after each successful trade execution. The fee is a small USDC
    transfer on Polygon to the OrangeBot developer wallet.

    This is non-blocking: fee failures never interrupt or affect the
    user's actual trading. Fees are also logged locally.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._fee_pct = Decimal(str(self._settings.orangebot_fee_pct))
        self._fee_wallet = self._settings.orangebot_fee_wallet
        self._fee_log: list[FeeRecord] = []
        self._total_fees_collected: Decimal = Decimal("0")

    def calculate_fee(self, trade_size_usd: Decimal) -> Decimal:
        """Calculate the fee amount for a given trade size."""
        return (trade_size_usd * self._fee_pct).quantize(Decimal("0.000001"))

    async def collect(
        self,
        trade_size_usd: Decimal,
        market: str = "unknown",
    ) -> FeeRecord:
        """
        Collect the developer fee for a completed trade.

        This is called fire-and-forget after trade execution.
        Failures are logged but never raise or block user trades.

        Args:
            trade_size_usd: The total USD value of the trade
            market: Market name/question for logging purposes

        Returns:
            FeeRecord with the outcome
        """
        fee_amount = self.calculate_fee(trade_size_usd)
        settings = self._settings

        # Skip in dry run mode
        if settings.dry_run:
            record = FeeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trade_size_usd=float(trade_size_usd),
                fee_usd=float(fee_amount),
                fee_pct=float(self._fee_pct * 100),
                market=market[:60],
                tx_hash=None,
                status="skipped_dry_run",
            )
            self._fee_log.append(record)
            log.debug(
                "Fee skipped (dry run)",
                fee_usd=f"${float(fee_amount):.4f}",
                market=market[:40],
            )
            return record

        # Skip if wallet or private key not configured
        if not settings.wallet_address or not settings.private_key:
            record = FeeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trade_size_usd=float(trade_size_usd),
                fee_usd=float(fee_amount),
                fee_pct=float(self._fee_pct * 100),
                market=market[:60],
                tx_hash=None,
                status="skipped_no_wallet",
            )
            self._fee_log.append(record)
            return record

        # Attempt fee transfer
        try:
            tx_hash = await self._send_fee(fee_amount)
            self._total_fees_collected += fee_amount

            record = FeeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trade_size_usd=float(trade_size_usd),
                fee_usd=float(fee_amount),
                fee_pct=float(self._fee_pct * 100),
                market=market[:60],
                tx_hash=tx_hash,
                status="sent",
            )
            self._fee_log.append(record)

            log.info(
                "OrangeBot fee collected",
                fee_usd=f"${float(fee_amount):.4f}",
                tx=tx_hash[:16] + "..." if tx_hash else "none",
                market=market[:40],
            )
            return record

        except Exception as e:
            log.warning(
                "Fee collection failed (trade unaffected)",
                error=str(e),
                fee_usd=f"${float(fee_amount):.4f}",
            )
            record = FeeRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trade_size_usd=float(trade_size_usd),
                fee_usd=float(fee_amount),
                fee_pct=float(self._fee_pct * 100),
                market=market[:60],
                tx_hash=None,
                status=f"failed: {str(e)[:80]}",
            )
            self._fee_log.append(record)
            return record

    async def collect_async_background(
        self,
        trade_size_usd: Decimal,
        market: str = "unknown",
    ) -> None:
        """
        Fire-and-forget fee collection. Does not block the caller.
        Safe to call without awaiting.
        """
        asyncio.create_task(self.collect(trade_size_usd, market))

    async def _send_fee(self, fee_amount: Decimal) -> str:
        """
        Send USDC fee to OrangeBot developer wallet on Polygon.

        Returns the transaction hash.
        """
        from web3 import Web3

        settings = self._settings
        private_key = settings.private_key.get_secret_value()
        sender = Web3.to_checksum_address(settings.wallet_address)
        recipient = Web3.to_checksum_address(self._fee_wallet)

        # USDC has 6 decimals
        fee_in_usdc_units = int(fee_amount * Decimal("1000000"))

        # Skip dust (less than $0.001)
        if fee_in_usdc_units < 1:
            log.debug("Fee too small to send (dust), skipping", fee_units=fee_in_usdc_units)
            return "dust_skipped"

        # Use proxy if configured (for geo-restrictions)
        rpc_url = settings.polygon_rpc_url
        w3 = Web3(Web3.HTTPProvider(rpc_url))

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT_POLYGON),
            abi=USDC_ABI,
        )

        nonce = w3.eth.get_transaction_count(sender)
        gas_price = w3.eth.gas_price

        tx = usdc.functions.transfer(recipient, fee_in_usdc_units).build_transaction({
            "from": sender,
            "nonce": nonce,
            "gas": 65000,
            "gasPrice": int(gas_price * 1.1),  # 10% buffer
            "chainId": settings.chain_id,
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        return tx_hash.hex()

    def get_fee_summary(self) -> dict:
        """Return a summary of fees collected this session."""
        sent = [r for r in self._fee_log if r.status == "sent"]
        return {
            "total_fees_usd": float(self._total_fees_collected),
            "fee_events_sent": len(sent),
            "fee_events_total": len(self._fee_log),
            "fee_pct": float(self._fee_pct * 100),
            "fee_wallet": self._fee_wallet,
        }

    def get_recent_fees(self, limit: int = 20) -> list[FeeRecord]:
        """Return the most recent fee records."""
        return self._fee_log[-limit:]


# Global singleton
_fee_collector: Optional[FeeCollector] = None


def get_fee_collector() -> FeeCollector:
    """Get the global fee collector instance."""
    global _fee_collector
    if _fee_collector is None:
        _fee_collector = FeeCollector()
    return _fee_collector
