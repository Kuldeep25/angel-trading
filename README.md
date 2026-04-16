# AI Trading Agent — Angel One SmartAPI

Full-stack personal trading agent with backtesting, live/paper trading, options support, voice commands, and Telegram/WhatsApp notifications.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11 + FastAPI + Angel One SmartAPI |
| Frontend | Angular 21 + Bootstrap 5 (dark theme) |
| Charts | Chart.js via ng2-charts |
| Code Editor | Monaco Editor (VS Code engine) |
| Notifications | Telegram Bot API + Twilio WhatsApp |
| Voice | SpeechRecognition (backend) + Web Speech API (browser) |

---

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Copy .env.example to .env and fill in your credentials
copy .env.example .env
```

**`.env` required fields:**
```
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_CODE=your_client_code
ANGEL_PASSWORD=your_pin
ANGEL_TOTP_SECRET=your_totp_base32_secret
TELEGRAM_BOT_TOKEN=optional
TELEGRAM_CHAT_ID=optional
TWILIO_ACCOUNT_SID=optional
TWILIO_AUTH_TOKEN=optional
TWILIO_FROM_WHATSAPP=whatsapp:+14155238886
TWILIO_TO_WHATSAPP=whatsapp:+91XXXXXXXXXX
```

**Start backend:**
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API docs: http://localhost:8000/docs

---

### 2. Frontend

```bash
cd frontend
npm install
ng serve
```

Open: http://localhost:4200

**Production build:**
```bash
ng build --configuration production
```

---

## Features

### Dashboard
- View all strategies with live/paper mode toggles
- Enable/disable individual strategies
- One-click backtest, edit, copy, delete

### Strategy Editor
- Monaco editor with Python syntax highlighting
- Save/load strategy `.py` files via API
- Categories: equity, futures, options

### Backtest Engine
- 1-year+ historical data via Angel One API (auto-chunked)
- Stop-loss % + trailing stop-loss %
- Equity curve chart + full trade log
- Metrics: Total Return, Max Drawdown, Win Rate, Sharpe Ratio

### Live Trading
- Start strategies in paper or live mode
- Auto-refresh positions every 5 seconds
- Per-position and bulk exit
- Live PnL tracking

### Voice Commands
- Browser mic via Web Speech API (en-IN)
- Fallback to server-side SpeechRecognition
- Supported commands listed in UI
- Full command history

---

## Strategy Format

Every strategy is a Python file with a `Strategy` class:

```python
import pandas as pd

class Strategy:
    def generate(self, df: pd.DataFrame) -> pd.Series:
        # df columns: timestamp, open, high, low, close, volume
        # Return: 1 = buy, -1 = sell, 0 = hold
        ...
```

---

## Folder Structure

```
angle-tarding/
├── backend/
│   ├── main.py                     # FastAPI entry point
│   ├── config/settings.py          # Pydantic BaseSettings
│   ├── angel/                      # AngelClient + instrument master
│   ├── data/                       # Historical data engine + normalizer
│   ├── strategy/                   # Loader, manager, sample strategies
│   ├── backtest/                   # Simulation engine + metrics
│   ├── options/                    # Strike selection + expiry utils
│   ├── execution/                  # Live + paper trading engines
│   ├── voice/                      # SpeechRecognition engine
│   ├── notifications/              # Telegram + WhatsApp
│   └── api/routes/                 # FastAPI routers (6 modules)
└── frontend/
    └── src/app/
        ├── features/
        │   ├── dashboard/
        │   ├── backtest/
        │   ├── live-trading/
        │   ├── strategy-editor/
        │   └── voice/
        └── core/services/api.service.ts
```
