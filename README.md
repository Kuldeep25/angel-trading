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

## Getting Started (Fresh Machine)

### Prerequisites

| Tool | Min Version | Install |
|---|---|---|
| Python | 3.11 | https://python.org |
| Node.js | 18 | https://nodejs.org |
| Angular CLI | 17 | `npm install -g @angular/cli` |
| Git | any | https://git-scm.com |

---

### Step 1 — Clone

```bash
git clone https://github.com/your-username/angle-tarding.git
cd angle-tarding
```

---

### Step 2 — Backend

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv

.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac / Linux

# Install dependencies
pip install -r requirements.txt

# Copy example env file
copy .env.example .env          # Windows
cp .env.example .env            # Mac / Linux
```

Open the `.env` file and fill in your values:

```env
# Angel One credentials (required)
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_CODE=your_client_code
ANGEL_PASSWORD=your_pin
ANGEL_TOTP_SECRET=your_totp_base32_secret

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Twilio WhatsApp (optional)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
TWILIO_WHATSAPP_TO=whatsapp:+91XXXXXXXXXX

# Mobile access — add your LAN / Tailscale IP (optional)
EXTRA_CORS_ORIGINS=http://192.168.x.x:4200,http://100.x.x.x:4200
```

> **Where to get Angel One credentials:**
> - Log in to Angel One → My Profile → API → Create App → copy the API Key
> - For `ANGEL_TOTP_SECRET`: go to Security → Enable 2FA → when the QR is shown,
>   use an authenticator app that reveals the base-32 secret (e.g. Authy)

---

### Step 3 — Frontend

```bash
cd frontend
npm install
```

---

### Step 4 — Run

Open **two separate terminals**:

**Terminal 1 — Backend (port 8000)**
```bash
cd backend
.venv\Scripts\activate          # Windows / skip on Mac+Linux if already active
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 — Frontend (port 4200)**
```bash
cd frontend
ng serve --host 0.0.0.0
```

Open in browser: **http://localhost:4200**
API docs / Swagger UI: http://localhost:8000/docs

---

## Mobile Access

### Same Wi-Fi (home network)

1. Find your PC's local IP
   - Windows: run `ipconfig` → look for **IPv4 Address** (e.g. `192.168.0.110`)
   - Mac/Linux: run `ip addr` or `ifconfig`
2. Add to `.env`: `EXTRA_CORS_ORIGINS=http://192.168.0.110:4200`
3. Restart the backend
4. Make sure your Wi-Fi is set to **Private** network in Windows Settings
5. Open on phone: `http://192.168.0.110:4200`

### From anywhere — Tailscale (recommended for trading apps)

Tailscale creates an encrypted private network between your devices — no port forwarding, no public exposure.

1. Install [Tailscale](https://tailscale.com/download) on your PC and phone
2. Sign in with the **same account** on both devices
3. Check your PC's Tailscale IP at https://login.tailscale.com/admin/machines (e.g. `100.103.x.x`)
4. Add to `.env`: `EXTRA_CORS_ORIGINS=http://100.103.x.x:4200`
5. Restart the backend
6. Open on phone (from anywhere): `http://100.103.x.x:4200`

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
