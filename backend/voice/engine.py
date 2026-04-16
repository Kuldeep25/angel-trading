"""
Voice command engine — microphone capture via SpeechRecognition
and text-based command parsing against voice_commands.json.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_COMMANDS_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "voice_commands.json")


# ─────────────────────────────────────────────────────────────────────────────
#  Command registry (loaded once)
# ─────────────────────────────────────────────────────────────────────────────

def _load_commands() -> Dict[str, Dict]:
    try:
        with open(_COMMANDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("commands", {})
    except Exception as exc:
        logger.error("Failed to load voice_commands.json: %s", exc)
        return {}


_COMMANDS: Dict[str, Dict] = _load_commands()


# ─────────────────────────────────────────────────────────────────────────────
#  Microphone capture (optional — requires pyaudio)
# ─────────────────────────────────────────────────────────────────────────────

def listen_once(timeout: int = 10, phrase_time_limit: int = 8) -> Optional[str]:
    """
    Listen to the microphone and return the transcribed text.
    Returns None on failure or silence.

    Requires: SpeechRecognition + pyaudio installed.
    """
    try:
        import speech_recognition as sr  # type: ignore
    except ImportError:
        logger.error("speech_recognition is not installed. pip install SpeechRecognition pyaudio")
        return None

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True

    try:
        with sr.Microphone() as source:
            logger.info("Voice: adjusting for ambient noise…")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Voice: listening (timeout=%ds)…", timeout)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
    except Exception as exc:
        logger.warning("Voice capture error: %s", exc)
        return None

    try:
        text = recognizer.recognize_google(audio, language="en-IN")
        logger.info("Voice recognised: '%s'", text)
        return text.lower().strip()
    except Exception as exc:
        logger.warning("Speech recognition error: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Command parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_command(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Match free-form text against the command registry.

    Returns
    -------
    (action, params) or (None, {}) if no match.

    Examples
    --------
    parse_command("buy nifty call")    → ("buy_call",   {"symbol": "NIFTY"})
    parse_command("set stop loss 20")  → ("set_stoploss", {"value": 20.0})
    parse_command("sell all positions") → ("exit_all",  {})
    """
    text = text.lower().strip()

    for pattern, cmd in _COMMANDS.items():
        result = _match(pattern.lower(), text)
        if result is not None:
            return cmd["action"], result

    logger.warning("No voice command match for: '%s'", text)
    return None, {}


def execute_text_command(text: str) -> Dict[str, Any]:
    """
    Parse and dispatch a text command to the appropriate trading action.
    Returns a result dict.
    """
    action, params = parse_command(text)

    if action is None:
        return {"status": "error", "message": f"Unknown command: '{text}'"}

    logger.info("Voice action: %s  params: %s", action, params)

    # Lazy import to avoid circular deps
    from execution.engine import place_order
    from execution.position_tracker import get_all_positions
    from angel.symbols import get_token
    from options.engine import get_spot_price, get_atm_strike, get_straddle_contracts

    try:
        if action == "exit_all":
            from execution.paper_trading import paper_engine
            from angel.client import angel_client
            # Exit paper positions
            paper_positions = paper_engine.get_positions()
            for pos in paper_positions:
                sym = pos["symbol"]
                ltp = get_spot_price(sym.replace("-EQ", ""), "NSE") or 0.0
                paper_engine.exit_position(sym, ltp)
            return {"status": "ok", "action": action, "message": "All paper positions exited."}

        elif action == "show_positions":
            data = get_all_positions()
            return {"status": "ok", "action": action, "data": data}

        elif action == "show_pnl":
            data = get_all_positions()
            return {
                "status": "ok",
                "action": action,
                "live_pnl": data["live_pnl"],
                "paper_pnl": data["paper_pnl"],
            }

        elif action in ("buy_call", "buy_put", "sell_call", "sell_put"):
            symbol = params.get("symbol", "NIFTY")
            option_type = "CE" if "call" in action else "PE"
            side = "BUY" if action.startswith("buy") else "SELL"
            spot = get_spot_price(symbol, "NSE") or 0.0
            strike = get_atm_strike(spot, symbol)
            return {
                "status": "ok",
                "action": action,
                "message": f"Voice command received: {side} {symbol} {option_type} strike={strike}. Route to execution.",
                "symbol": symbol,
                "option_type": option_type,
                "side": side,
                "strike": strike,
            }

        elif action in ("buy_equity", "sell_equity"):
            symbol = params.get("symbol", "")
            side = "BUY" if action == "buy_equity" else "SELL"
            return {"status": "ok", "action": action, "symbol": symbol, "side": side}

        elif action == "set_stoploss":
            value = params.get("value", 0.0)
            return {"status": "ok", "action": action, "stoploss": value}

        elif action in ("start_trading", "stop_trading"):
            return {"status": "ok", "action": action}

        else:
            return {"status": "error", "message": f"Unhandled action: {action}"}

    except Exception as exc:
        logger.exception("Voice command dispatch error")
        return {"status": "error", "message": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
#  Pattern matching helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match(pattern: str, text: str) -> Optional[Dict[str, Any]]:
    """
    Simple template matcher that supports {symbol} and {value} placeholders.
    Returns a dict of extracted values, or None if no match.
    """
    # Build a regex from the pattern
    param_names: list[str] = []

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        param_names.append(name)
        if name == "value":
            return r"([0-9]+(?:\.[0-9]+)?)"
        return r"([a-z0-9]+)"

    regex_str = re.sub(r"\{(\w+)\}", _replace, re.escape(pattern))
    regex_str = regex_str.replace(r"\ ", r"\s+")  # allow extra spaces

    m = re.fullmatch(regex_str.strip(), text.strip())
    if m is None:
        return None

    result: Dict[str, Any] = {}
    for i, name in enumerate(param_names):
        raw = m.group(i + 1)
        if name == "symbol":
            result[name] = raw.upper()
        elif name == "value":
            result[name] = float(raw)
        else:
            result[name] = raw
    return result
