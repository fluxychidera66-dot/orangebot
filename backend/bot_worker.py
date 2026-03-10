"""
OrangeBot Bot Worker
====================
Launched by runner.py for each user.
Actually connects to Polymarket and executes arbitrage trades.
"""

import asyncio
import os
import sys
import traceback
from datetime import datetime, timezone
from supabase import create_client, Client

# ── ENV ──
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
USER_ID = os.environ["ORANGEBOT_USER_ID"]
WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "")
POLY_API_KEY = os.environ.get("POLY_API_KEY", "")
POLY_API_SECRET = os.environ.get("POLY_API_SECRET", "")
POLY_API_PASSPHRASE = os.environ.get("POLY_API_PASSPHRASE", "")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
MIN_PROFIT = float(os.environ.get("MIN_PROFIT_THRESHOLD", "0.015"))
MAX_POSITION = float(os.environ.get("MAX_POSITION_SIZE", "100"))
FEE_WALLET = "0x8446df20DAae168bBFfBb6774d1a3D1D9b17f970"
FEE_PCT = 0.01

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def log(msg):
    print(f"[{datetime.now().isoformat()}][{USER_ID[:8]}] {msg}", flush=True)

async def record_trade(market_question, yes_ask, no_ask, trade_size, expected_profit, status="filled"):
    try:
        sb.table("trades").insert({
            "user_id": USER_ID,
            "market_question": market_question,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "combined_cost": yes_ask + no_ask,
            "trade_size_usd": trade_size,
            "expected_profit": expected_profit,
            "status": status,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        # Record fee
        fee_usd = round(trade_size * FEE_PCT, 6)
        sb.table("fee_logs").insert({
            "user_id": USER_ID,
            "fee_type": "trade",
            "trade_size_usd": trade_size,
            "fee_usd": fee_usd,
            "fee_pct": FEE_PCT * 100,
            "status": "sent",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        log(f"Trade recorded — size: ${trade_size} profit: ${expected_profit} fee: ${fee_usd}")
    except Exception as e:
        log(f"Failed to record trade: {e}")

def is_bot_stopped():
    try:
        result = sb.table("bot_instances").select("status").eq("user_id", USER_ID).single().execute()
        return result.data.get("status") == "stopped"
    except:
        return False

async def run_bot():
    log(f"Bot worker starting — dry_run={DRY_RUN} min_profit={MIN_PROFIT}")

    try:
        from py_clob_client.client import ClobClient
        from polymarket_apis.markets import MarketsClient
    except ImportError as e:
        log(f"Import error: {e}")
        sb.table("bot_instances").update({
            "status": "error",
            "error_message": f"Missing dependency: {e}",
        }).eq("user_id", USER_ID).execute()
        return

    # Init clients
    try:
        clob = ClobClient(
            "https://clob.polymarket.com",
            key=POLY_API_KEY,
            secret=POLY_API_SECRET,
            passphrase=POLY_API_PASSPHRASE,
            chain_id=137,
            signature_type=2,
        )
        log("CLOB client initialized")
    except Exception as e:
        log(f"CLOB init failed: {e}")
        sb.table("bot_instances").update({
            "status": "error",
            "error_message": f"API connection failed: {str(e)[:200]}",
        }).eq("user_id", USER_ID).execute()
        return

    scan_count = 0

    while True:
        # Check if user stopped bot
        if is_bot_stopped():
            log("Stop signal received — shutting down")
            break

        try:
            # Fetch active markets
            markets_resp = clob.get_markets(next_cursor="MA==")
            markets = markets_resp.get("data", []) if markets_resp else []
            scan_count += 1

            if scan_count % 10 == 0:
                log(f"Scanning... {len(markets)} markets found")

            for market in markets:
                if is_bot_stopped():
                    break

                try:
                    tokens = market.get("tokens", [])
                    if len(tokens) < 2:
                        continue

                    token_yes = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
                    token_no = next((t for t in tokens if t.get("outcome", "").upper() == "NO"), None)

                    if not token_yes or not token_no:
                        continue

                    yes_id = token_yes.get("token_id")
                    no_id = token_no.get("token_id")

                    if not yes_id or not no_id:
                        continue

                    # Get orderbook prices
                    yes_book = clob.get_order_book(yes_id)
                    no_book = clob.get_order_book(no_id)

                    yes_asks = yes_book.asks if yes_book and yes_book.asks else []
                    no_asks = no_book.asks if no_book and no_book.asks else []

                    if not yes_asks or not no_asks:
                        continue

                    yes_ask = float(min(a.price for a in yes_asks))
                    no_ask = float(min(a.price for a in no_asks))
                    combined = yes_ask + no_ask

                    profit_pct = (1.0 - combined) / combined

                    if profit_pct >= MIN_PROFIT:
                        question = market.get("question", "Unknown market")
                        trade_size = min(MAX_POSITION, 50.0)
                        expected_profit = round(trade_size * profit_pct, 4)

                        log(f"ARBITRAGE FOUND: {question[:60]} YES={yes_ask} NO={no_ask} profit={profit_pct:.2%}")

                        if not DRY_RUN:
                            # Execute real trades
                            try:
                                from py_clob_client.clob_types import OrderArgs, OrderType
                                from py_clob_client.order_builder.constants import BUY

                                yes_order = clob.create_and_post_order(OrderArgs(
                                    token_id=yes_id,
                                    price=yes_ask,
                                    size=trade_size / 2,
                                    side=BUY,
                                ))
                                no_order = clob.create_and_post_order(OrderArgs(
                                    token_id=no_id,
                                    price=no_ask,
                                    size=trade_size / 2,
                                    side=BUY,
                                ))
                                log(f"Orders placed — YES: {yes_order} NO: {no_order}")
                                await record_trade(question, yes_ask, no_ask, trade_size, expected_profit, "filled")

                            except Exception as order_err:
                                log(f"Order failed: {order_err}")
                                await record_trade(question, yes_ask, no_ask, trade_size, expected_profit, "failed")
                        else:
                            # Dry run — just record
                            await record_trade(question, yes_ask, no_ask, trade_size, expected_profit, "dry_run")

                except Exception as market_err:
                    continue

            await asyncio.sleep(5)

        except Exception as e:
            log(f"Scan error: {e}")
            await asyncio.sleep(10)

    log("Bot worker stopped")

if __name__ == "__main__":
    asyncio.run(run_bot())
