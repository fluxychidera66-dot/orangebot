"""Data models for OrangeBot."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class Token:
    """A YES or NO token in a prediction market."""
    token_id: str
    outcome: str  # "YES" or "NO"


@dataclass
class Market:
    """A Polymarket prediction market."""
    condition_id: str
    question: str
    yes_token: Token
    no_token: Token
    volume: Decimal = Decimal("0")
    liquidity: Decimal = Decimal("0")
    yes_price: Decimal = Decimal("0.5")
    no_price: Decimal = Decimal("0.5")
    end_date: Optional[datetime] = None
    active: bool = True
    closed: bool = False
    neg_risk: bool = False


@dataclass
class OrderLevel:
    """A single price level in an order book."""
    price: Decimal
    size: Decimal


@dataclass
class OrderBook:
    """Order book for a single token."""
    token_id: str
    bids: list[OrderLevel] = field(default_factory=list)
    asks: list[OrderLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> Optional[Decimal]:
        if not self.bids:
            return None
        return max(b.price for b in self.bids)

    @property
    def best_ask(self) -> Optional[Decimal]:
        if not self.asks:
            return None
        return min(a.price for a in self.asks)

    @property
    def best_ask_size(self) -> Decimal:
        if not self.asks:
            return Decimal("0")
        best = min(self.asks, key=lambda a: a.price)
        return best.size


@dataclass
class ArbitrageOpportunity:
    """A detected arbitrage opportunity."""
    market: Market
    yes_ask: Decimal
    no_ask: Decimal
    combined_cost: Decimal
    profit_pct: Decimal
    yes_size_available: Decimal
    no_size_available: Decimal
    max_trade_size: Decimal = Decimal("0")

    @property
    def expected_profit_usd(self) -> Decimal:
        """Expected profit in USD for max_trade_size."""
        payout = self.max_trade_size * Decimal("1")
        cost = self.max_trade_size * self.combined_cost
        return payout - cost
