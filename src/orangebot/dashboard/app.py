"""OrangeBot web dashboard — FastAPI application."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

app = FastAPI(title="OrangeBot Dashboard", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)


def check_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> bool:
    settings = get_settings()
    if not settings.dashboard_password:
        return True  # No auth configured
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    import hmac
    ok = (
        hmac.compare_digest(credentials.username, settings.dashboard_username)
        and hmac.compare_digest(credentials.password, settings.dashboard_password)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.get("/api/stats")
async def get_stats(_: bool = Depends(check_auth)) -> JSONResponse:
    """Return current bot stats."""
    from orangebot.data.repositories import StatsHistoryRepository, TradeRepository
    try:
        trades = await TradeRepository.get_summary()
        history = await StatsHistoryRepository.get_recent(limit=1)
        latest = history[0] if history else {}
        return JSONResponse({
            "status": "running",
            "trades": trades.get("cnt", 0) or 0,
            "total_profit": trades.get("total_profit", 0.0) or 0.0,
            "markets_monitored": latest.get("markets", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/trades")
async def get_trades(limit: int = 50, _: bool = Depends(check_auth)) -> JSONResponse:
    from orangebot.data.repositories import TradeRepository
    rows = await TradeRepository.get_recent(limit=limit)
    return JSONResponse(rows)


@app.get("/api/balance")
async def get_balance(_: bool = Depends(check_auth)) -> JSONResponse:
    from orangebot.tracking.portfolio import PortfolioTracker
    tracker = PortfolioTracker()
    balances = await tracker.get_current_balances()
    return JSONResponse(balances)


@app.get("/api/near-misses")
async def get_near_misses(_: bool = Depends(check_auth)) -> JSONResponse:
    from orangebot.data.repositories import NearMissAlertRepository
    rows = await NearMissAlertRepository.get_recent(limit=50)
    return JSONResponse(rows)


@app.get("/api/fees")
async def get_fees(_: bool = Depends(check_auth)) -> JSONResponse:
    from orangebot.fees.collector import get_fee_collector
    collector = get_fee_collector()
    return JSONResponse(collector.get_fee_summary())


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>🍊 OrangeBot Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f0f0f; color: #e0e0e0; }
    header { background: #1a1a1a; border-bottom: 2px solid #f97316;
             padding: 16px 24px; display: flex; align-items: center; gap: 12px; }
    header h1 { font-size: 1.4rem; color: #f97316; }
    header .version { font-size: 0.75rem; color: #666; margin-top: 2px; }
    .fee-notice { background: #1c1207; border: 1px solid #7c3d12;
                  padding: 8px 16px; font-size: 0.75rem; color: #fb923c;
                  text-align: center; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px; padding: 24px; }
    .card { background: #1a1a1a; border: 1px solid #2a2a2a;
            border-radius: 8px; padding: 20px; }
    .card .label { font-size: 0.75rem; color: #888; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 8px; }
    .card .value { font-size: 1.8rem; font-weight: 700; color: #f97316; }
    .card .sub { font-size: 0.75rem; color: #666; margin-top: 4px; }
    .section { padding: 0 24px 24px; }
    .section h2 { font-size: 1rem; color: #aaa; margin-bottom: 12px;
                  border-bottom: 1px solid #2a2a2a; padding-bottom: 8px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th { text-align: left; padding: 8px 12px; color: #666;
         font-weight: 500; border-bottom: 1px solid #2a2a2a; }
    td { padding: 8px 12px; border-bottom: 1px solid #1e1e1e; }
    tr:hover td { background: #1e1e1e; }
    .profit { color: #4ade80; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
             font-size: 0.72rem; font-weight: 600; }
    .badge-live { background: #14532d; color: #4ade80; }
    .badge-dry { background: #713f12; color: #fbbf24; }
    .refresh { font-size: 0.72rem; color: #555; margin-left: auto; }
    footer { text-align: center; padding: 24px; color: #444; font-size: 0.75rem; }
  </style>
</head>
<body>
  <header>
    <span style="font-size:2rem">🍊</span>
    <div>
      <h1>OrangeBot Dashboard</h1>
      <div class="version">v1.0.0 · Polymarket Arbitrage Bot</div>
    </div>
    <div class="refresh" id="lastRefresh"></div>
  </header>
  <div class="fee-notice">
    💰 Transparent 1% developer fee per trade · Fee wallet: 0x8446df20DAae168bBFfBb6774d1a3D1D9b17f970
  </div>

  <div class="grid" id="statsGrid">
    <div class="card"><div class="label">Status</div><div class="value" id="status">–</div></div>
    <div class="card"><div class="label">USDC Balance</div><div class="value" id="balance">–</div><div class="sub">Polygon wallet</div></div>
    <div class="card"><div class="label">Total Trades</div><div class="value" id="trades">–</div></div>
    <div class="card"><div class="label">Expected Profit</div><div class="value profit" id="profit">–</div></div>
    <div class="card"><div class="label">Markets Monitored</div><div class="value" id="markets">–</div></div>
    <div class="card"><div class="label">Fees Collected</div><div class="value" id="fees">–</div><div class="sub">Developer fee (1%)</div></div>
  </div>

  <div class="section">
    <h2>Recent Trades</h2>
    <table>
      <thead><tr>
        <th>Time</th><th>Market</th><th>Side</th>
        <th>Price</th><th>Size</th><th>Expected P/L</th>
      </tr></thead>
      <tbody id="tradeBody"><tr><td colspan="6" style="color:#555;text-align:center;padding:20px">Loading...</td></tr></tbody>
    </table>
  </div>

  <footer>OrangeBot v1.0.0 · Free to use · MIT License</footer>

  <script>
    async function fetchJSON(url) {
      try { const r = await fetch(url); return await r.json(); } catch { return null; }
    }

    function fmt(ts) {
      if (!ts) return '–';
      return new Date(ts).toLocaleTimeString();
    }

    async function refresh() {
      const [stats, balance, trades, fees] = await Promise.all([
        fetchJSON('/api/stats'),
        fetchJSON('/api/balance'),
        fetchJSON('/api/trades?limit=20'),
        fetchJSON('/api/fees'),
      ]);

      if (stats) {
        document.getElementById('status').textContent = stats.status || '–';
        document.getElementById('trades').textContent = stats.trades ?? '–';
        document.getElementById('profit').textContent = stats.total_profit != null
          ? '$' + parseFloat(stats.total_profit).toFixed(4) : '–';
        document.getElementById('markets').textContent = stats.markets_monitored ?? '–';
      }
      if (balance) {
        document.getElementById('balance').textContent =
          '$' + parseFloat(balance.polymarket_usdc || 0).toFixed(2);
      }
      if (fees) {
        document.getElementById('fees').textContent =
          '$' + parseFloat(fees.total_fees_usd || 0).toFixed(4);
      }

      if (trades && Array.isArray(trades)) {
        const body = document.getElementById('tradeBody');
        if (!trades.length) {
          body.innerHTML = '<tr><td colspan="6" style="color:#555;text-align:center;padding:20px">No trades yet</td></tr>';
        } else {
          body.innerHTML = trades.map(t => `
            <tr>
              <td>${fmt(t.timestamp)}</td>
              <td>${(t.market_name||'').slice(0,40)}</td>
              <td>${t.side?.toUpperCase() || '–'} ${t.outcome || ''}</td>
              <td>$${parseFloat(t.price||0).toFixed(4)}</td>
              <td>$${parseFloat(t.size||0).toFixed(2)}</td>
              <td class="profit">${t.profit_expected != null ? '$'+parseFloat(t.profit_expected).toFixed(4) : '–'}</td>
            </tr>`).join('');
        }
      }

      document.getElementById('lastRefresh').textContent =
        'Last updated: ' + new Date().toLocaleTimeString();
    }

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard(_: bool = Depends(check_auth)) -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


def run_dashboard(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the dashboard web server."""
    settings = get_settings()
    log.info("Starting OrangeBot dashboard", host=host, port=port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
