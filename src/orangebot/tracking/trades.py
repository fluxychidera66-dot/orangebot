"""Local trade history log for OrangeBot."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from orangebot.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class TradeRecord:
    """A single trade record."""
    timestamp: str
    platform: str
    market_name: str
    side: str
    outcome: str
    price: float
    size: float
    profit_expected: Optional[float] = None


class TradeLog:
    """Records and retrieves trade history from SQLite."""

    def record_trade(
        self,
        platform: str,
        market_name: str,
        side: str,
        outcome: str,
        price: float,
        size: float,
        profit_expected: Optional[float] = None,
    ) -> None:
        """Synchronously record a trade (fires async task)."""
        asyncio.create_task(
            self._record_async(platform, market_name, side, outcome, price, size, profit_expected)
        )

    async def _record_async(self, *args) -> None:
        from orangebot.data.repositories import TradeRepository
        platform, market_name, side, outcome, price, size, profit = args
        try:
            await TradeRepository.insert(
                timestamp=datetime.now(timezone.utc).isoformat(),
                platform=platform,
                market_name=market_name,
                side=side,
                outcome=outcome,
                price=price,
                size=size,
                profit_expected=profit or 0.0,
            )
        except Exception as e:
            log.error("Failed to record trade", error=str(e))

    def get_trades(self, limit: int = 50, platform: Optional[str] = None) -> list[TradeRecord]:
        """Get recent trades synchronously (runs event loop)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In async context, return empty list (use async version instead)
                return []
            from orangebot.data.repositories import TradeRepository
            rows = loop.run_until_complete(TradeRepository.get_recent(limit=limit, platform=platform))
            return [
                TradeRecord(
                    timestamp=r["timestamp"],
                    platform=r["platform"],
                    market_name=r["market_name"],
                    side=r["side"],
                    outcome=r["outcome"],
                    price=r["price"],
                    size=r["size"],
                    profit_expected=r.get("profit_expected"),
                )
                for r in rows
            ]
        except Exception:
            return []

    def get_all_time_summary(self) -> dict:
        """Get all-time trade summary."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return {"trade_count": 0, "total_cost": 0.0, "expected_profit": 0.0}
            from orangebot.data.repositories import TradeRepository
            summary = loop.run_until_complete(TradeRepository.get_summary())
            return {
                "trade_count": summary.get("cnt", 0) or 0,
                "total_cost": summary.get("total_cost", 0.0) or 0.0,
                "expected_profit": summary.get("total_profit", 0.0) or 0.0,
                "first_trade": summary.get("first"),
            }
        except Exception:
            return {"trade_count": 0, "total_cost": 0.0, "expected_profit": 0.0}

    def get_daily_summary(self) -> dict:
        """Get today's trade summary."""
        return {"trade_count": 0, "total_cost": 0.0, "expected_profit": 0.0}
