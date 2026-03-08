"""
OrangeBot Multi-Tenant Runner
==============================
Polls Supabase for bot_instances with status='starting'
and spins up a bot process for each user.
Runs continuously on Railway — one instance manages all users.
"""

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from supabase import create_client, Client

# ── CONFIG ──
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
POLL_INTERVAL = 15  # seconds between checks for new bot requests

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Active bot tasks: user_id -> asyncio.Task
active_bots: dict[str, asyncio.Task] = {}


async def run_user_bot(user_id: str, wallet: dict, bot_config: dict) -> None:
    """Run a single user's OrangeBot instance."""
    import tempfile
    import os as _os

    print(f"[{datetime.now().isoformat()}] Starting bot for user {user_id[:8]}...")

    try:
        # Update status to running
        sb.table("bot_instances").update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "error_message": None,
        }).eq("user_id", user_id).execute()

        # Set up environment for this user's bot
        env = {
            **_os.environ,
            "PRIVATE_KEY": wallet.get("private_key", ""),
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

        # Launch bot subprocess
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "orangebot.bot_worker",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Heartbeat loop — check if user stopped bot every 30s
        while True:
            await asyncio.sleep(30)

            # Check if user requested stop
            result = sb.table("bot_instances") \
                .select("status") \
                .eq("user_id", user_id) \
                .single() \
                .execute()

            current_status = result.data.get("status") if result.data else "stopped"

            if current_status == "stopped":
                print(f"[{datetime.now().isoformat()}] User {user_id[:8]} requested stop.")
                proc.terminate()
                break

            # Update heartbeat
            sb.table("bot_instances").update({
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", user_id).execute()

            # Check if process died
            if proc.returncode is not None:
                raise RuntimeError(f"Bot process exited with code {proc.returncode}")

        await proc.wait()
        print(f"[{datetime.now().isoformat()}] Bot stopped for user {user_id[:8]}")

    except asyncio.CancelledError:
        print(f"[{datetime.now().isoformat()}] Bot task cancelled for user {user_id[:8]}")

    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Bot error for user {user_id[:8]}: {e}")
        sb.table("bot_instances").update({
            "status": "error",
            "error_message": str(e)[:500],
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        }).eq("user_id", user_id).execute()
        return

    finally:
        # Clean up
        active_bots.pop(user_id, None)

    # Mark stopped
    sb.table("bot_instances").update({
        "status": "stopped",
        "stopped_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).execute()


async def get_user_wallet(user_id: str) -> Optional[dict]:
    """Fetch wallet credentials from private schema via service role."""
    try:
        result = sb.table("wallets") \
            .select("*") \
            .eq("user_id", user_id) \
            .single() \
            .execute()
        return result.data
    except Exception as e:
        print(f"Failed to fetch wallet for {user_id[:8]}: {e}")
        return None


async def main_loop() -> None:
    """Main polling loop — checks for new bot start requests."""
    print(f"[{datetime.now().isoformat()}] OrangeBot Runner started. Polling every {POLL_INTERVAL}s...")

    while True:
        try:
            # Find all bots that want to start
            result = sb.table("bot_instances") \
                .select("*, wallets(*)") \
                .eq("status", "starting") \
                .execute()

            for bot in (result.data or []):
                user_id = bot["user_id"]

                # Skip if already running
                if user_id in active_bots and not active_bots[user_id].done():
                    continue

                wallet = bot.get("wallets")
                if not wallet:
                    sb.table("bot_instances").update({
                        "status": "error",
                        "error_message": "No wallet configured. Please add your wallet credentials first.",
                    }).eq("user_id", user_id).execute()
                    continue

                # Validate credentials exist
                if not wallet.get("wallet_address") or not wallet.get("poly_api_key"):
                    sb.table("bot_instances").update({
                        "status": "error",
                        "error_message": "Missing wallet address or API credentials.",
                    }).eq("user_id", user_id).execute()
                    continue

                # Start bot task
                task = asyncio.create_task(
                    run_user_bot(user_id, wallet, bot)
                )
                active_bots[user_id] = task
                print(f"[{datetime.now().isoformat()}] Launched bot for user {user_id[:8]}")

            # Clean up finished tasks
            done_users = [uid for uid, task in active_bots.items() if task.done()]
            for uid in done_users:
                active_bots.pop(uid, None)

            print(f"[{datetime.now().isoformat()}] Active bots: {len(active_bots)}")

        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Runner error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


def handle_shutdown(sig, frame):
    print(f"\n[{datetime.now().isoformat()}] Shutting down runner...")
    for task in active_bots.values():
        task.cancel()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    asyncio.run(main_loop())
