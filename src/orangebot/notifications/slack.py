"""Slack (and optional Telegram) notifications for OrangeBot."""

from typing import Optional

import aiohttp

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)


class Notifier:
    """Sends notifications to Slack webhook."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def send_message(self, text: str) -> bool:
        """Send a message to Slack. Returns True on success."""
        settings = self._settings
        if not settings.slack_webhook_url:
            return False

        try:
            session = await self._get_session()
            async with session.post(
                settings.slack_webhook_url,
                json={"text": f"🍊 OrangeBot: {text}"},
            ) as resp:
                return resp.status == 200
        except Exception as e:
            log.debug("Slack notification failed", error=str(e))
            return False

    async def notify_startup(self, mode: str = "LIVE") -> None:
        await self.send_message(f"Bot started in *{mode}* mode")

    async def notify_shutdown(self, reason: str = "normal") -> None:
        await self.send_message(f"Bot stopped ({reason})")

    async def notify_trade(
        self,
        market: str,
        profit: float,
        size: float,
    ) -> None:
        msg = (
            f"✅ Trade executed\n"
            f"Market: {market[:50]}\n"
            f"Size: ${size:.2f} | Expected profit: ${profit:.4f}"
        )
        await self.send_message(msg)

    async def notify_circuit_breaker(self, reason: str) -> None:
        await self.send_message(f"⚠️ Circuit breaker triggered: {reason}")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# Singleton
_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
