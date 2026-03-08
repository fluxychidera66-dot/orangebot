"""Polymarket Gamma API client for fetching market listings."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import aiohttp

from orangebot.api.models import Market, Token
from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


class GammaClient:
    """Async HTTP client for the Polymarket Gamma API."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "OrangeBot/1.0"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "GammaClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get_markets(
        self,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch markets from Gamma API."""
        settings = get_settings()
        session = await self._get_session()

        params: dict[str, Any] = {
            "active": str(active).lower(),
            "closed": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }

        # Filter by resolution horizon
        params["end_date_max"] = ""  # no max by default

        try:
            async with session.get(
                f"{settings.gamma_base_url}/markets",
                params=params,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data if isinstance(data, list) else data.get("markets", [])
        except Exception as e:
            log.error("Gamma API error", error=str(e))
            return []

    def parse_market(self, raw: dict) -> Optional[Market]:
        """Parse a raw Gamma API market response into a Market object."""
        try:
            tokens = raw.get("tokens", [])
            if len(tokens) < 2:
                return None

            yes_token = None
            no_token = None
            for t in tokens:
                outcome = t.get("outcome", "").upper()
                if outcome == "YES":
                    yes_token = Token(token_id=t["token_id"], outcome="YES")
                elif outcome == "NO":
                    no_token = Token(token_id=t["token_id"], outcome="NO")

            if not yes_token or not no_token:
                return None

            # Parse end date
            end_date = None
            end_date_str = raw.get("end_date_iso") or raw.get("end_date")
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(
                        end_date_str.replace("Z", "+00:00")
                    )
                    if end_date.tzinfo is None:
                        end_date = end_date.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            return Market(
                condition_id=raw.get("condition_id", raw.get("id", "")),
                question=raw.get("question", "Unknown"),
                yes_token=yes_token,
                no_token=no_token,
                volume=Decimal(str(raw.get("volume", 0) or 0)),
                liquidity=Decimal(str(raw.get("liquidity", 0) or 0)),
                yes_price=Decimal(str(raw.get("best_ask", 0.5) or 0.5)),
                no_price=Decimal(str(1 - float(raw.get("best_ask", 0.5) or 0.5))),
                end_date=end_date,
                active=raw.get("active", True),
                closed=raw.get("closed", False),
                neg_risk=raw.get("neg_risk", False),
            )
        except Exception as e:
            log.debug("Failed to parse market", error=str(e))
            return None

    async def get_all_active_markets(self, min_liquidity: float = 10000.0) -> list[Market]:
        """Fetch and parse all active markets above liquidity threshold."""
        markets = []
        offset = 0
        limit = 100

        while True:
            raw_markets = await self.get_markets(active=True, limit=limit, offset=offset)
            if not raw_markets:
                break

            for raw in raw_markets:
                m = self.parse_market(raw)
                if m and float(m.liquidity) >= min_liquidity:
                    markets.append(m)

            if len(raw_markets) < limit:
                break

            offset += limit

        log.info("Fetched active markets", count=len(markets), min_liquidity=min_liquidity)
        return markets
