"""Polymarket CLOB API client for orderbooks and order placement."""

import asyncio
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from orangebot.api.models import OrderBook, OrderLevel
from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

CLOB_BASE = "https://clob.polymarket.com"

# Neg-risk status cache shared across all client instances
_neg_risk_cache: dict[str, bool] = {}


class ClobClient:
    """Async HTTP client for the Polymarket CLOB API."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._settings = get_settings()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "OrangeBot/1.0"},
            )
        return self._session

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Generate L2 authentication headers for Polymarket CLOB."""
        settings = self._settings
        if not settings.poly_api_key:
            return {}

        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        secret = settings.poly_api_secret.get_secret_value() if settings.poly_api_secret else ""
        signature = hmac.new(
            secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()

        return {
            "POLY-API-KEY": settings.poly_api_key,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": (
                settings.poly_api_passphrase.get_secret_value()
                if settings.poly_api_passphrase
                else ""
            ),
        }

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "ClobClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get_orderbook(self, token_id: str) -> OrderBook:
        """Fetch the order book for a single token."""
        session = await self._get_session()
        url = f"{self._settings.clob_base_url}/book?token_id={token_id}"

        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()

            bids = [
                OrderLevel(price=Decimal(str(b["price"])), size=Decimal(str(b["size"])))
                for b in data.get("bids", [])
            ]
            asks = [
                OrderLevel(price=Decimal(str(a["price"])), size=Decimal(str(a["size"])))
                for a in data.get("asks", [])
            ]
            return OrderBook(token_id=token_id, bids=bids, asks=asks)

        except Exception as e:
            log.debug("Orderbook fetch failed", token=token_id[:16], error=str(e))
            return OrderBook(token_id=token_id)

    async def get_orderbooks_batch(self, token_ids: list[str]) -> dict[str, OrderBook]:
        """Fetch multiple orderbooks concurrently."""
        tasks = [self.get_orderbook(tid) for tid in token_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        books = {}
        for tid, result in zip(token_ids, results):
            if isinstance(result, OrderBook):
                books[tid] = result
            else:
                books[tid] = OrderBook(token_id=tid)
        return books

    async def get_neg_risk(self, token_id: str) -> bool:
        """Check if a token is neg_risk type (cached)."""
        if token_id in _neg_risk_cache:
            return _neg_risk_cache[token_id]

        session = await self._get_session()
        url = f"{self._settings.clob_base_url}/neg-risk?token_id={token_id}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("neg_risk", False)
                else:
                    result = False
            _neg_risk_cache[token_id] = result
            return result
        except Exception:
            return False

    async def prefetch_neg_risk(self, token_ids: list[str]) -> None:
        """Pre-fetch neg_risk status for multiple tokens concurrently."""
        uncached = [t for t in token_ids if t not in _neg_risk_cache]
        if not uncached:
            return
        tasks = [self.get_neg_risk(t) for t in uncached]
        await asyncio.gather(*tasks, return_exceptions=True)
        log.debug("Prefetched neg_risk", count=len(uncached))

    async def place_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        size: Decimal,
        price: Decimal,
        neg_risk: bool = False,
    ) -> dict[str, Any]:
        """Place a limit order on the CLOB."""
        from orangebot.executor.signer import OrderSigner

        settings = self._settings
        signer = OrderSigner()

        if not signer.is_configured:
            raise RuntimeError("Wallet not configured for order placement")

        # Build and sign the order
        order_data = await signer.build_and_sign_order(
            token_id=token_id,
            side=side,
            size=size,
            price=price,
            neg_risk=neg_risk,
        )

        session = await self._get_session()
        path = "/order"
        body_str = str(order_data)

        headers = {
            "Content-Type": "application/json",
            **self._auth_headers("POST", path, body_str),
        }

        try:
            async with session.post(
                f"{settings.clob_base_url}{path}",
                json=order_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                if resp.status not in (200, 201):
                    log.warning("Order placement failed", status=resp.status, response=result)
                return result
        except asyncio.TimeoutError:
            log.error("Order placement timed out", token=token_id[:16])
            raise

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        session = await self._get_session()
        path = f"/order/{order_id}"
        headers = {
            "Content-Type": "application/json",
            **self._auth_headers("DELETE", path),
        }

        try:
            async with session.delete(
                f"{self._settings.clob_base_url}{path}",
                headers=headers,
            ) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            log.error("Cancel order failed", order_id=order_id, error=str(e))
            return False

    async def get_order(self, order_id: str) -> Optional[dict]:
        """Get order status by ID."""
        session = await self._get_session()
        path = f"/order/{order_id}"
        headers = self._auth_headers("GET", path)

        try:
            async with session.get(
                f"{self._settings.clob_base_url}{path}",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception:
            return None

    async def get_positions(self) -> list[dict]:
        """Get current open positions for the configured wallet."""
        settings = self._settings
        if not settings.wallet_address:
            return []

        session = await self._get_session()
        url = f"https://data-api.polymarket.com/positions?user={settings.wallet_address}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            log.error("Failed to fetch positions", error=str(e))
            return []
