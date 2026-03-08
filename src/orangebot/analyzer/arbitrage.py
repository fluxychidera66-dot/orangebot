"""Arbitrage opportunity analyzer for OrangeBot."""

from decimal import Decimal
from typing import Optional

from orangebot.api.models import ArbitrageOpportunity, Market, OrderBook
from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)


class MarketSnapshot:
    """A snapshot of a market with its current orderbooks."""

    def __init__(
        self,
        market: Market,
        yes_book: OrderBook,
        no_book: OrderBook,
    ) -> None:
        self.market = market
        self.yes_book = yes_book
        self.no_book = no_book


class ArbitrageAnalyzer:
    """
    Detects YES+NO arbitrage opportunities.

    An opportunity exists when:
        best_ask(YES) + best_ask(NO) < $1.00

    Because at resolution, exactly one side pays $1.00,
    so buying both always yields $1.00 minus combined cost.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._stats = {
            "markets_analyzed": 0,
            "opportunities_found": 0,
        }

    def analyze(self, snapshot: "MarketSnapshot") -> Optional[ArbitrageOpportunity]:
        """Analyze a single market snapshot for arbitrage."""
        self._stats["markets_analyzed"] += 1
        settings = self._settings

        yes_ask = snapshot.yes_book.best_ask
        no_ask = snapshot.no_book.best_ask

        if yes_ask is None or no_ask is None:
            return None

        combined = yes_ask + no_ask
        threshold = Decimal(str(1.0 - settings.min_profit_threshold))

        if combined >= threshold:
            return None

        profit_pct = (Decimal("1") - combined) / Decimal("1")

        yes_size = snapshot.yes_book.best_ask_size
        no_size = snapshot.no_book.best_ask_size

        self._stats["opportunities_found"] += 1

        return ArbitrageOpportunity(
            market=snapshot.market,
            yes_ask=yes_ask,
            no_ask=no_ask,
            combined_cost=combined,
            profit_pct=profit_pct,
            yes_size_available=yes_size,
            no_size_available=no_size,
        )

    def analyze_batch(
        self, snapshots: list["MarketSnapshot"]
    ) -> list[ArbitrageOpportunity]:
        """Analyze a batch of snapshots, return sorted opportunities."""
        opportunities = []
        for snap in snapshots:
            opp = self.analyze(snap)
            if opp is not None:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.profit_pct, reverse=True)
        return opportunities

    def get_stats(self) -> dict:
        return dict(self._stats)
