"""
OrangeBot Multi-Tenant Runner
Polls Supabase on every HTTP request + background thread
"""

import os
import threading
import time
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PORT = int(os.environ.get("PORT", 10000))

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
active_bots: dict = {}

def poll_and_start_bots():
    try:
        # Get starting bots
        bots_result = sb.table("bot_instances").select("*").eq("status", "starting").execute()
        bots = bots_result.data or []
        print(f"[{datetime.now().isoformat()}] Found {len(bots)} bots with status=starting")

        for bot in bots:
            user_id = bot["user_id"]
            if user_id in active_bots:
                continue

            # Fetch wallet separately
            wallet_result = sb.table("wallets").select("*").eq("user_id", user_id).execute()
            wallet = wallet_result.data[0] if wallet_result.data else None
            print(f"[{datetime.now().isoformat()}] Wallet for {user_id[:8]}: {wallet is not None}")

            if not wallet:
                sb.table("bot_instances").update({
                    "status": "error",
                    "error_message": "No wallet configured.",
                }).eq("user_id", user_id).execute()
                continue

            if not wallet.get("wallet_address") or not wallet.get("poly_api_key"):
                sb.table("bot_instances").update({
                    "status": "error",
                    "error_message": "Missing wallet address or API key.",
                }).eq("user_id", user_id).execute()
                continue

            # Mark as running
            sb.table("bot_instances").update({
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "error_message": None,
            }).eq("user_id", user_id).execute()

            # Launch actual bot worker process
            bot_env = {
                **os.environ,
                "ORANGEBOT_USER_ID": user_id,
                "WALLET_ADDRESS": wallet.get("wallet_address", ""),
                "POLY_API_KEY": wallet.get("poly_api_key", ""),
                "POLY_API_SECRET": wallet.get("poly_api_secret", ""),
                "POLY_API_PASSPHRASE": wallet.get("poly_api_passphrase", ""),
                "DRY_RUN": str(bot.get("dry_run", True)).lower(),
                "MIN_PROFIT_THRESHOLD": str(float(bot.get("min_profit_pct", 1.5)) / 100),
                "MAX_POSITION_SIZE": str(bot.get("max_position_usd", 100)),
            }

            proc = subprocess.Popen(
                [sys.executable, "backend/bot_worker.py"],
                env=bot_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            active_bots[user_id] = {
                "started_at": datetime.now().isoformat(),
                "wallet": wallet.get("wallet_address", ""),
                "proc": proc,
            }
            print(f"[{datetime.now().isoformat()}] Bot worker launched for user {user_id[:8]} PID={proc.pid}")

        # Check for stopped bots — kill their processes
        stopped = sb.table("bot_instances").select("user_id").eq("status", "stopped").execute()
        for bot in (stopped.data or []):
            uid = bot["user_id"]
            if uid in active_bots:
                proc = active_bots[uid].get("proc")
                if proc:
                    try:
                        proc.terminate()
                    except:
                        pass
                del active_bots[uid]

        # Heartbeat
        for user_id in list(active_bots.keys()):
            sb.table("bot_instances").update({
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", user_id).execute()

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Poll error: {e}")


def background_poller():
    """Poll every 15 seconds in background thread."""
    while True:
        poll_and_start_bots()
        time.sleep(15)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        poll_and_start_bots()
        self.send_response(200)
        self.end_headers()
        try:
            test = sb.table("bot_instances").select("*").execute()
            rows = test.data or []
            msg = f"OrangeBot Runner — Active bots: {len(active_bots)} — DB rows: {len(rows)} — Statuses: {[r.get('status') for r in rows]}"
        except Exception as e:
            msg = f"OrangeBot Runner — DB ERROR: {e}"
        self.wfile.write(msg.encode())
        print(f"[{datetime.now().isoformat()}] {msg}")

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"[{datetime.now().isoformat()}] OrangeBot Runner starting on port {PORT}...")
    # Test Supabase connection on startup
    try:
        test = sb.table("bot_instances").select("count").execute()
        print(f"[{datetime.now().isoformat()}] Supabase connection OK: {test.data}")
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Supabase connection FAILED: {e}")
    
    # Start background polling thread
    t = threading.Thread(target=background_poller, daemon=True)
    t.start()
    
    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[{datetime.now().isoformat()}] Ready.")
    server.serve_forever()
