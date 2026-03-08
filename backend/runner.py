"""
OrangeBot Multi-Tenant Runner
Polls Supabase on every HTTP request + background thread
"""

import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PORT = int(os.environ.get("PORT", 10000))

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
active_bots: dict = {}

def poll_and_start_bots():
    """Check Supabase for bots that need to start."""
    try:
        result = sb.table("bot_instances").select("*, wallets(*)").eq("status", "starting").execute()
        bots = result.data or []
        print(f"[{datetime.now().isoformat()}] Polling — found {len(bots)} bots with status=starting")
        
        for bot in bots:
            user_id = bot["user_id"]
            if user_id in active_bots:
                continue

            wallet = bot.get("wallets") or bot.get("wallets", None)
            print(f"Bot data keys: {list(bot.keys())}")
            print(f"Wallet data: {wallet}")
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

            active_bots[user_id] = {
                "started_at": datetime.now().isoformat(),
                "wallet": wallet.get("wallet_address", ""),
            }
            print(f"[{datetime.now().isoformat()}] Bot started for user {user_id[:8]}")

        # Check for stopped bots
        stopped = sb.table("bot_instances").select("user_id").eq("status", "stopped").execute()
        for bot in (stopped.data or []):
            uid = bot["user_id"]
            if uid in active_bots:
                del active_bots[uid]
                print(f"[{datetime.now().isoformat()}] Bot stopped for user {uid[:8]}")

        # Heartbeat for running bots
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
