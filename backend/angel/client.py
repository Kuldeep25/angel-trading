"""
Angel One SmartConnect client — singleton with auto token refresh.
"""
from __future__ import annotations

import threading
import time
import pyotp
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import so tests without the package still work
try:
    from SmartApi import SmartConnect  # type: ignore
except ImportError:  # pragma: no cover
    SmartConnect = None  # type: ignore


class AngelClient:
    """Thread-safe singleton wrapper around SmartConnect."""

    _instance: Optional["AngelClient"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "AngelClient":
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                cls._instance = obj
        return cls._instance

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def connect(self, api_key: str, client_code: str, password: str, totp_secret: str) -> None:
        """Login to Angel One and store session tokens."""
        if SmartConnect is None:
            raise RuntimeError(
                "smartapi-python is not installed. "
                "Run: pip install smartapi-python"
            )

        self._api_key = api_key
        self._client_code = client_code
        self._password = password
        self._totp_secret = totp_secret

        self._smart_api = SmartConnect(api_key=api_key)
        self._do_login()
        self._initialized = True
        logger.info("Angel One session established for %s", client_code)

        # Start background refresh thread (refresh ~every 6 hours)
        self._stop_refresh = threading.Event()
        self._refresh_thread = threading.Thread(
            target=self._token_refresh_loop, daemon=True
        )
        self._refresh_thread.start()

    def disconnect(self) -> None:
        """Logout and stop background thread."""
        if not self._initialized:
            return
        self._stop_refresh.set()
        try:
            self.smart_api.terminateSession(self._client_code)
            logger.info("Angel One session terminated.")
        except Exception as exc:
            logger.warning("Logout error: %s", exc)
        self._initialized = False

    @property
    def smart_api(self) -> "SmartConnect":
        self._check_initialized()
        return self._smart_api

    @property
    def auth_token(self) -> str:
        self._check_initialized()
        return self._auth_token

    @property
    def feed_token(self) -> str:
        self._check_initialized()
        return self._feed_token

    @property
    def refresh_token(self) -> str:
        self._check_initialized()
        return self._refresh_token

    @property
    def is_connected(self) -> bool:
        return self._initialized

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _do_login(self) -> None:
        totp = pyotp.TOTP(self._totp_secret).now()
        data = self._smart_api.generateSession(
            self._client_code, self._password, totp
        )
        if not data or not data.get("status"):
            raise ConnectionError(f"Angel One login failed: {data}")
        self._auth_token = data["data"]["jwtToken"]
        self._refresh_token = data["data"]["refreshToken"]
        self._feed_token = self._smart_api.getfeedToken()

    def _refresh_tokens(self) -> None:
        try:
            data = self._smart_api.generateToken(self._refresh_token)
            if data and data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                self._refresh_token = data["data"]["refreshToken"]
                self._feed_token = self._smart_api.getfeedToken()
                logger.info("Angel One tokens refreshed.")
            else:
                logger.warning("Token refresh returned: %s – re-logging in.", data)
                self._do_login()
        except Exception as exc:
            logger.error("Token refresh error: %s – attempting re-login.", exc)
            try:
                self._do_login()
            except Exception as login_exc:
                logger.critical("Re-login failed: %s", login_exc)

    def _token_refresh_loop(self) -> None:
        """Refresh tokens every 6 hours in the background."""
        interval = 6 * 60 * 60  # seconds
        while not self._stop_refresh.wait(timeout=interval):
            self._refresh_tokens()

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "AngelClient is not connected. Call connect() first."
            )


# Module-level singleton
angel_client = AngelClient()
