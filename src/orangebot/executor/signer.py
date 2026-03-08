"""Order signing for Polymarket CLOB using EIP-712."""

import time
from decimal import Decimal
from typing import Any, Optional

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

# Polymarket CLOB exchange addresses on Polygon
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

SIDE_BUY = 0
SIDE_SELL = 1


class OrderSigner:
    """Signs Polymarket orders using the configured wallet private key."""

    def __init__(self) -> None:
        self._settings = get_settings()

    @property
    def is_configured(self) -> bool:
        """True if private key and wallet address are set."""
        return (
            self._settings.private_key is not None
            and self._settings.wallet_address is not None
        )

    async def build_and_sign_order(
        self,
        token_id: str,
        side: str,
        size: Decimal,
        price: Decimal,
        neg_risk: bool = False,
    ) -> dict[str, Any]:
        """
        Build and sign a Polymarket limit order.

        Uses py-clob-client's order builder under the hood for
        correct EIP-712 signing compatible with Polymarket's CLOB.
        """
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType

        settings = self._settings
        private_key = settings.private_key.get_secret_value()

        # Determine exchange address
        exchange = NEG_RISK_CTF_EXCHANGE if neg_risk else CTF_EXCHANGE

        # Build order via py_clob_client
        client = ClobClient(
            host=settings.clob_base_url,
            key=private_key,
            chain_id=settings.chain_id,
            signature_type=0,  # EOA signing
            funder=settings.wallet_address,
        )

        side_int = SIDE_BUY if side.upper() == "BUY" else SIDE_SELL

        order_args = OrderArgs(
            token_id=token_id,
            price=float(price),
            size=float(size),
            side=side_int,
            fee_rate_bps=0,
            nonce=0,
            expiration=0,
        )

        signed_order = client.create_order(order_args)

        return {
            "order": signed_order.dict() if hasattr(signed_order, "dict") else signed_order,
            "owner": settings.wallet_address,
            "orderType": "GTC",
        }
