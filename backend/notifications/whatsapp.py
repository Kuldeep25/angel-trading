"""
WhatsApp notifications via Twilio WhatsApp Sandbox.

Setup:
  1. Sign up for Twilio (free trial): https://www.twilio.com
  2. Activate WhatsApp sandbox: Twilio Console → Messaging → Try it out → Send a WhatsApp message
  3. From your phone, send the join code to +1 415 523 8886
  4. Set TWILIO_* environment variables in .env
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from twilio.rest import Client as TwilioClient  # type: ignore
    _TWILIO_AVAILABLE = True
except ImportError:
    _TWILIO_AVAILABLE = False
    logger.warning("twilio not installed. WhatsApp notifications disabled.")


class WhatsAppNotifier:
    """Sends WhatsApp messages via Twilio."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        to_number: str,
    ) -> None:
        self._from = from_number  # "whatsapp:+14155238886"
        self._to   = to_number    # "whatsapp:+91XXXXXXXXXX"
        self._client: Optional[Any] = None  # type: ignore[type-arg]

        if _TWILIO_AVAILABLE and account_sid and auth_token and to_number:
            self._client = TwilioClient(account_sid, auth_token)

    def send(self, message: str) -> bool:
        """Send a WhatsApp message. Returns True on success."""
        if self._client is None:
            logger.debug("Twilio client not configured — skipping WhatsApp message.")
            return False
        try:
            msg = self._client.messages.create(
                body=message,
                from_=self._from,
                to=self._to,
            )
            logger.info("WhatsApp sent: SID=%s", msg.sid)
            return True
        except Exception as exc:
            logger.error("WhatsApp send error: %s", exc)
            return False

    def send_trade_alert(self, trade: dict) -> bool:
        side = trade.get("side", "")
        symbol = trade.get("symbol", "")
        price  = trade.get("price", "")
        qty    = trade.get("quantity", "")
        mode   = trade.get("mode", "live").upper()
        msg = (
            f"[{mode}] Trade Alert\n"
            f"{side} {symbol} @ ₹{price} x{qty}"
        )
        return self.send(msg)

    def send_pnl_update(self, live_pnl: float, paper_pnl: float) -> bool:
        msg = f"PnL Update\nLive: ₹{live_pnl:,.2f}\nPaper: ₹{paper_pnl:,.2f}"
        return self.send(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

from typing import Any  # noqa: E402 (already imported above but needed for re-export)

_wa_notifier: Optional[WhatsAppNotifier] = None


def init_whatsapp(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
) -> None:
    global _wa_notifier
    _wa_notifier = WhatsAppNotifier(account_sid, auth_token, from_number, to_number)
    logger.info("WhatsApp notifier initialised.")


def get_wa_notifier() -> Optional[WhatsAppNotifier]:
    return _wa_notifier
