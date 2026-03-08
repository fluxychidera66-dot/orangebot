# 🍊 OrangeBot — Polymarket Arbitrage Trading Bot

> Automated, high-performance arbitrage bot for Polymarket prediction markets.  
> Free to use · Open source · Real-time WebSocket execution

---

## What is OrangeBot?

OrangeBot scans Polymarket prediction markets 24/7 for arbitrage opportunities — situations where you can buy both YES and NO shares in the same market for a combined cost below $1.00. Since one side always pays out $1.00 at resolution, this locks in a **risk-free profit**.

**Example:**

| YES Ask | NO Ask | Combined Cost | Payout | Profit |
|---------|--------|---------------|--------|--------|
| $0.48   | $0.49  | $0.97         | $1.00  | ~3.1%  |

OrangeBot finds these gaps in real time and executes both sides automatically.

---

## Features

- **Real-time WebSocket scanning** — 6 parallel connections, monitors up to 1,500 markets
- **Automatic arbitrage detection & execution** — buys YES + NO when combined cost is below threshold
- **Risk management** — position sizing, circuit breakers, drawdown limits
- **Low-latency execution** — async HTTP/2, parallel order signing
- **Web dashboard** — live order and stats visibility
- **Geo bypass** — SOCKS5 proxy support for non-US regions
- **Auto-redemption** — automatically redeems resolved positions to USDC

---

## Transparent Fee Disclosure

OrangeBot is **completely free** to download and use.

To support development, OrangeBot charges a **1% developer fee** on each executed trade:

- ✅ **Transparent** — disclosed in code, README, and dashboard
- ✅ **Automatic** — small USDC transfer to developer wallet after each trade
- ✅ **Non-blocking** — fee failures never affect your trades
- ✅ **Safe margins** — default profit threshold is **1.5%**, so your net profit after the fee is always positive

**Developer wallet:** `0x8446df20DAae168bBFfBb6774d1a3D1D9b17f970` (Polygon/USDC)

> Verify in `src/orangebot/config.py` and `src/orangebot/fees/collector.py`

---

## Strategy

Pure arbitrage: when `YES ask + NO ask < $1.00`, buying both locks in guaranteed profit.

OrangeBot:
1. Scans order books in real time via WebSocket
2. Sizes positions by configurable risk %
3. Applies circuit breakers and pre-trade filters
4. Executes when profit threshold is met (default 1.5%)

---

## Setup

**Requirements:** Python 3.12+, Polygon wallet with USDC

```bash
git clone https://github.com/yourusername/orangebot.git
cd orangebot
pip install -e .
cp .env.example .env
# Edit .env with your credentials
```

**Generate Polymarket L2 credentials:**
```bash
python -c "
from py_clob_client.client import ClobClient
import os
client = ClobClient('https://clob.polymarket.com', key=os.environ['PRIVATE_KEY'], chain_id=137)
creds = client.create_or_derive_api_creds()
print(f'POLY_API_KEY={creds.api_key}')
print(f'POLY_API_SECRET={creds.api_secret}')
print(f'POLY_API_PASSPHRASE={creds.api_passphrase}')
"
```

**Approve USDC (one-time):**
```bash
python scripts/approve_usdc.py
```

**Run:**
```bash
orangebot run --live --realtime     # Live trading
orangebot run --dry-run --realtime  # Test mode (no real trades)
```

---

## Key Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PRIVATE_KEY` | — | Wallet private key (`0x...`) |
| `WALLET_ADDRESS` | — | Your wallet address |
| `POLY_API_KEY/SECRET/PASSPHRASE` | — | Polymarket L2 credentials |
| `MIN_PROFIT_THRESHOLD` | `0.015` | 1.5% min profit (after 1% fee you keep 0.5%+) |
| `MAX_POSITION_SIZE` | `100` | Max USD per trade |
| `DRY_RUN` | `true` | Set `false` for live trading |

See `.env.example` for full configuration reference.

---

## Commands

```bash
orangebot run        # Run the bot
orangebot scan       # One-time scan for opportunities
orangebot markets    # List active markets
orangebot status     # Bot status and recent trades
orangebot balance    # Wallet balances
orangebot positions  # Open and redeemable positions
orangebot redeem     # Redeem resolved positions to USDC
orangebot pnl        # Profit/loss summary
orangebot dashboard  # Web dashboard (http://localhost:8080)
```

---

## License

MIT — free to use, modify, and distribute.

*OrangeBot is not financial advice. Trading involves risk.*
