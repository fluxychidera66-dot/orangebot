"""
OrangeBot Multi-Tenant Runner
Runs a basic HTTP server alongside the bot poller so Render stays happy.
"""

import asyncio
import os
import signal
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PORT = int(os.environ.get("PORT", 10000))

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
active_bots: dict[str, asyncio.Task] = {}

# ── DUMMY HTTP SERVER (keeps Render free tier happy) ──
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        active = len(active_bots)
        self.wfile.write(f"🍊 OrangeBot Runner — Active bots: {active}".encode())
    def log_message(self, format, *args):
        pass  # silence access logs

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[{datetime.now().isoformat()}] Health server running on port {PORT}")

# ── BOT RUNNER ──
async def run_user_bot(user_id: str, wallet: dict, bot_config: dict) -> None:
    print(f"[{datetime.now().isoformat()}] Starting bot for user {user_id[:8]}...")
    try:
        sb.table("bot_instances").update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error_message": None,
        }).eq("user_id", user_id).execute()

        env = {
            **os.environ,
            "WALLET_ADDRESS": wallet.get("wallet_address", ""),
            "POLY_API_KEY": wallet.get("poly_api_key", ""),
            "POLY_API_SECRET": wallet.get("poly_api_secret", ""),
            "POLY_API_PASSPHRASE": wallet.get("poly_api_passphrase", ""),
            "POLYGON_RPC_URL": wallet.get("polygon_rpc_url", "https://polygon-rpc.com"),
            "MIN_PROFIT_THRESHOLD": str(float(bot_config.get("min_profit_pct", 1.5)) / 100),
            "MAX_POSITION_SIZE": str(bot_config.get("max_position_usd", 100)),
            "DRY_RUN": str(bot_config.get("dry_run", True)).lower(),
            "ORANGEBOT_USER_ID": user_id,
            "SUPABASE_URL": SUPABASE_URL,
            "SUPABASE_SERVICE_KEY": SUPABASE_SERVICE_KEY,
        }

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "orangebot.bot_worker",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        while True:
            await asyncio.sleep(30)
            result = sb.table("bot_instances").select("status").eq("user_id", user_id).single().execute()
            current_status = result.data.get("status") if result.data else "stopped"

            if current_status == "stopped":
                proc.terminate()
                break

            sb.table("bot_instances").update({
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", user_id).execute()

            if proc.returncode is not None:
                raise RuntimeError(f"Bot process exited with code {proc.returncode}")

        await proc.wait()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Bot error for user {user_id[:8]}: {e}")
        sb.table("bot_instances").update({
            "status": "error",
            "error_message": str(e)[:500],
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", user_id).execute()
        return
    finally:
        active_bots.pop(user_id, None)

    sb.table("bot_instances").update({
        "status": "stopped",
        "stopped_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).execute()


async def main_loop() -> None:
    print(f"[{datetime.now().isoformat()}] OrangeBot Runner started. Polling every 15s...")
    while True:
        try:
            result = sb.table("bot_instances").select("*, wallets(*)").eq("status", "starting").execute()
            for bot in (result.data or []):
                user_id = bot["user_id"]
                if user_id in active_bots and not active_bots[user_id].done():
                    continue
                wallet = bot.get("wallets")
                if not wallet:
                    sb.table("bot_instances").update({
                        "status": "error",
                        "error_message": "No wallet configured.",
                    }).eq("user_id", user_id).execute()
                    continue
                task = asyncio.create_task(run_user_bot(user_id, wallet, bot))
                active_bots[user_id] = task

            done_users = [uid for uid, task in active_bots.items() if task.done()]
            for uid in done_users:
                active_bots.pop(uid, None)

            print(f"[{datetime.now().isoformat()}] Active bots: {len(active_bots)}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Runner error: {e}")

        await asyncio.sleep(15)


if __name__ == "__main__":
    start_health_server()
    asyncio.run(main_loop())
