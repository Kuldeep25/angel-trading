"""
Telegram notification bot using python-telegram-bot v20+ (async).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from telegram import Bot  # type: ignore
    from telegram.error import TelegramError  # type: ignore
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Telegram notifications disabled.")


class TelegramNotifier:
    """Sends trade alerts and PnL updates to a Telegram chat."""

    def __init__(self, token: str, chat_id: str) -> None:
        self._token   = token
        self._chat_id = chat_id
        self._bot: Optional[Any] = None

        if _TELEGRAM_AVAILABLE and token and chat_id:
            self._bot = Bot(token=token)

    async def send(self, message: str) -> bool:
        """Send a text message. Returns True on success."""
        if self._bot is None:
            logger.debug("Telegram bot not configured — skipping message.")
            return False
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="HTML",
            )
            return True
        except Exception as exc:
            logger.error("Telegram send error: %s", exc)
            return False

    async def send_trade_alert(self, trade: Dict[str, Any]) -> bool:
        """Format and send a trade alert."""
        side_emoji = "🟢" if trade.get("side") == "BUY" else "🔴"
        msg = (
            f"{side_emoji} <b>Trade Alert</b>\n"
            f"Symbol : <code>{trade.get('symbol','')}</code>\n"
            f"Side   : <b>{trade.get('side','')}</b>\n"
            f"Qty    : {trade.get('quantity','')}\n"
            f"Price  : ₹{trade.get('price','')}\n"
            f"Mode   : {trade.get('mode','live').upper()}\n"
            f"Time   : {trade.get('time','')}"
        )
        return await self.send(msg)

    async def send_pnl_update(self, live_pnl: float, paper_pnl: float) -> bool:
        """Send a PnL summary message."""
        live_emoji  = "📈" if live_pnl  >= 0 else "📉"
        paper_emoji = "📊" if paper_pnl >= 0 else "📉"
        msg = (
            f"<b>PnL Update</b>\n"
            f"{live_emoji} Live  : ₹{live_pnl:,.2f}\n"
            f"{paper_emoji} Paper : ₹{paper_pnl:,.2f}"
        )
        return await self.send(msg)

    async def send_error(self, error: str) -> bool:
        """Send a system error alert."""
        msg = f"⚠️ <b>System Error</b>\n<code>{error}</code>"
        return await self.send(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  Module-level singleton (configured from settings at startup)
# ─────────────────────────────────────────────────────────────────────────────

_notifier: Optional[TelegramNotifier] = None


def init_telegram(token: str, chat_id: str) -> None:
    global _notifier
    _notifier = TelegramNotifier(token=token, chat_id=chat_id)
    logger.info("Telegram notifier initialised.")


def get_notifier() -> Optional[TelegramNotifier]:
    return _notifier
