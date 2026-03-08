"""Trade execution engine for OrangeBot."""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from orangebot.api.models import ArbitrageOpportunity
from orangebot.config import get_settings
from orangebot.executor.signer import OrderSigner
from orangebot.utils.logging import get_logger

log = get_logger(__name__)


class ExecutionStatus(Enum):
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    DRY_RUN = "dry_run"
    CANCELLED = "cancelled"


@dataclass
class ExecutionResult:
    """Result of an execution attempt."""
    status: ExecutionStatus
    opportunity: ArbitrageOpportunity
    yes_order_id: Optional[str] = None
    no_order_id: Optional[str] = None
    yes_filled: Decimal = Decimal("0")
    no_filled: Decimal = Decimal("0")
    expected_profit: Decimal = Decimal("0")
    actual_cost: Decimal = Decimal("0")
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class OrderExecutor:
    """
    Executes arbitrage trades on Polymarket.

    For each opportunity:
    1. Places YES buy order
    2. Places NO buy order simultaneously
    3. Monitors fill status
    4. Cancels unfilled legs after timeout
    """

    ORDER_TIMEOUT_SECONDS = 10

    def __init__(self) -> None:
        self._settings = get_settings()
        self.signer = OrderSigner()
        self._async_client: Optional[Any] = None
        self._stats = {
            "executions_attempted": 0,
            "executions_filled": 0,
            "executions_failed": 0,
            "total_volume_usd": 0.0,
        }

    async def _ensure_async_client(self) -> Optional[Any]:
        """Lazily initialize the async CLOB client."""
        if self._async_client is None:
            from orangebot.api.clob import ClobClient
            self._async_client = ClobClient()
        return self._async_client

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        detection_timestamp_ms: Optional[float] = None,
    ) -> ExecutionResult:
        """Execute an arbitrage opportunity by placing both YES and NO orders."""
        import time

        start_time = time.monotonic()
        settings = self._settings
        self._stats["executions_attempted"] += 1

        # Dry run — simulate without placing real orders
        if settings.dry_run:
            expected_profit = opportunity.max_trade_size * (
                Decimal("1") - opportunity.combined_cost
            )
            log.info(
                "[DRY RUN] Would execute arbitrage",
                market=opportunity.market.question[:40],
                size=float(opportunity.max_trade_size),
                yes_ask=float(opportunity.yes_ask),
                no_ask=float(opportunity.no_ask),
                combined=float(opportunity.combined_cost),
                profit_pct=f"{float(opportunity.profit_pct) * 100:.2f}%",
                expected_profit=f"${float(expected_profit):.4f}",
            )
            return ExecutionResult(
                status=ExecutionStatus.DRY_RUN,
                opportunity=opportunity,
                expected_profit=expected_profit,
            )

        # Live execution
        client = await self._ensure_async_client()
        market = opportunity.market
        size = opportunity.max_trade_size

        # Get neg_risk status for both tokens
        yes_neg_risk = await client.get_neg_risk(market.yes_token.token_id)
        no_neg_risk = await client.get_neg_risk(market.no_token.token_id)

        log.info(
            "Placing arbitrage orders",
            market=market.question[:40],
            size=float(size),
            yes_ask=float(opportunity.yes_ask),
            no_ask=float(opportunity.no_ask),
        )

        # Place both orders concurrently
        try:
            yes_task = asyncio.create_task(
                client.place_order(
                    token_id=market.yes_token.token_id,
                    side="BUY",
                    size=size,
                    price=opportunity.yes_ask,
                    neg_risk=yes_neg_risk,
                )
            )
            no_task = asyncio.create_task(
                client.place_order(
                    token_id=market.no_token.token_id,
                    side="BUY",
                    size=size,
                    price=opportunity.no_ask,
                    neg_risk=no_neg_risk,
                )
            )

            yes_result, no_result = await asyncio.gather(
                yes_task, no_task, return_exceptions=True
            )

        except Exception as e:
            self._stats["executions_failed"] += 1
            log.error("Order placement error", error=str(e))
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                opportunity=opportunity,
                error=str(e),
            )

        # Extract order IDs
        yes_order_id = None
        no_order_id = None

        if isinstance(yes_result, dict):
            yes_order_id = yes_result.get("orderID") or yes_result.get("id")
        if isinstance(no_result, dict):
            no_order_id = no_result.get("orderID") or no_result.get("id")

        if not yes_order_id or not no_order_id:
            error = f"Order placement failed: yes={yes_result}, no={no_result}"
            log.error("Missing order IDs", yes=yes_order_id, no=no_order_id)
            self._stats["executions_failed"] += 1
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                opportunity=opportunity,
                yes_order_id=yes_order_id,
                no_order_id=no_order_id,
                error=error,
            )

        # Wait for fills with timeout
        filled = await self._wait_for_fills(
            client, yes_order_id, no_order_id, timeout=self.ORDER_TIMEOUT_SECONDS
        )

        latency_ms = (time.monotonic() - start_time) * 1000

        if filled:
            expected_profit = size * (Decimal("1") - opportunity.combined_cost)
            actual_cost = size * opportunity.combined_cost
            self._stats["executions_filled"] += 1
            self._stats["total_volume_usd"] += float(actual_cost)

            # Log to trade database
            await self._record_trade(opportunity, size, expected_profit)

            log.info(
                "Arbitrage executed",
                market=market.question[:40],
                size=float(size),
                cost=f"${float(actual_cost):.2f}",
                profit=f"${float(expected_profit):.4f}",
                latency_ms=f"{latency_ms:.0f}ms",
            )

            return ExecutionResult(
                status=ExecutionStatus.FILLED,
                opportunity=opportunity,
                yes_order_id=yes_order_id,
                no_order_id=no_order_id,
                yes_filled=size,
                no_filled=size,
                expected_profit=expected_profit,
                actual_cost=actual_cost,
                latency_ms=latency_ms,
            )
        else:
            # Cancel any unfilled orders
            await asyncio.gather(
                client.cancel_order(yes_order_id),
                client.cancel_order(no_order_id),
                return_exceptions=True,
            )
            self._stats["executions_failed"] += 1
            log.warning(
                "Orders not filled, cancelled",
                market=market.question[:40],
                latency_ms=f"{latency_ms:.0f}ms",
            )
            return ExecutionResult(
                status=ExecutionStatus.CANCELLED,
                opportunity=opportunity,
                yes_order_id=yes_order_id,
                no_order_id=no_order_id,
                latency_ms=latency_ms,
            )

    async def _wait_for_fills(
        self,
        client: Any,
        yes_order_id: str,
        no_order_id: str,
        timeout: float = 10.0,
    ) -> bool:
        """Poll until both orders are filled or timeout is reached."""
        import time

        deadline = time.monotonic() + timeout
        poll_interval = 0.5

        while time.monotonic() < deadline:
            yes_status = await client.get_order(yes_order_id)
            no_status = await client.get_order(no_order_id)

            yes_filled = (
                yes_status and yes_status.get("status") in ("MATCHED", "FILLED")
            )
            no_filled = (
                no_status and no_status.get("status") in ("MATCHED", "FILLED")
            )

            if yes_filled and no_filled:
                return True

            await asyncio.sleep(poll_interval)

        return False

    async def _record_trade(
        self,
        opportunity: ArbitrageOpportunity,
        size: Decimal,
        profit: Decimal,
    ) -> None:
        """Record trade to local database."""
        from datetime import datetime, timezone
        from orangebot.tracking.trades import TradeLog

        trade_log = TradeLog()
        trade_log.record_trade(
            platform="polymarket",
            market_name=opportunity.market.question,
            side="buy",
            outcome="arb_yes_no",
            price=float(opportunity.combined_cost),
            size=float(size * opportunity.combined_cost),
            profit_expected=float(profit),
        )

    def get_stats(self) -> dict:
        return dict(self._stats)

    async def close(self) -> None:
        if self._async_client:
            await self._async_client.close()
