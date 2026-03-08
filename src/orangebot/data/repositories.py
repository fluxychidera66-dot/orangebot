"""SQLite repositories for OrangeBot data persistence."""

import aiosqlite
from pathlib import Path

DB_PATH = Path("orangebot.db")


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


class TradeRepository:
    """Stores executed trade records."""

    TABLE = """
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        platform TEXT NOT NULL,
        market_name TEXT NOT NULL,
        side TEXT NOT NULL,
        outcome TEXT NOT NULL,
        price REAL NOT NULL,
        size REAL NOT NULL,
        profit_expected REAL DEFAULT 0
    )
    """

    @classmethod
    async def ensure_table(cls) -> None:
        async with await _get_db() as db:
            await db.execute(cls.TABLE)
            await db.commit()

    @classmethod
    async def insert(cls, **kwargs) -> None:
        await cls.ensure_table()
        async with await _get_db() as db:
            await db.execute(
                """INSERT INTO trades
                   (timestamp, platform, market_name, side, outcome, price, size, profit_expected)
                   VALUES (:timestamp, :platform, :market_name, :side, :outcome, :price, :size, :profit_expected)""",
                kwargs,
            )
            await db.commit()

    @classmethod
    async def get_recent(cls, limit: int = 50, platform: str = None) -> list[dict]:
        await cls.ensure_table()
        async with await _get_db() as db:
            if platform:
                cursor = await db.execute(
                    "SELECT * FROM trades WHERE platform=? ORDER BY id DESC LIMIT ?",
                    (platform, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    async def get_summary(cls) -> dict:
        await cls.ensure_table()
        async with await _get_db() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt, SUM(size) as total_cost, SUM(profit_expected) as total_profit, MIN(timestamp) as first FROM trades"
            )
            row = await cursor.fetchone()
            return dict(row) if row else {}


class PortfolioRepository:
    """Stores balance snapshots over time."""

    TABLE = """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        polymarket_usdc REAL DEFAULT 0,
        total_usd REAL DEFAULT 0,
        positions_value REAL DEFAULT 0
    )
    """

    @classmethod
    async def ensure_table(cls) -> None:
        async with await _get_db() as db:
            await db.execute(cls.TABLE)
            await db.commit()

    @classmethod
    async def insert(cls, **kwargs) -> None:
        await cls.ensure_table()
        async with await _get_db() as db:
            await db.execute(
                """INSERT INTO portfolio_snapshots
                   (timestamp, polymarket_usdc, total_usd, positions_value)
                   VALUES (:timestamp, :polymarket_usdc, :total_usd, :positions_value)""",
                kwargs,
            )
            await db.commit()

    @classmethod
    async def get_recent(cls, limit: int = 100) -> list[dict]:
        await cls.ensure_table()
        async with await _get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


class StatsHistoryRepository:
    """Stores hourly bot statistics snapshots."""

    TABLE = """
    CREATE TABLE IF NOT EXISTS stats_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        hour TEXT NOT NULL,
        markets INTEGER DEFAULT 0,
        price_updates INTEGER DEFAULT 0,
        arbitrage_alerts INTEGER DEFAULT 0,
        executions_attempted INTEGER DEFAULT 0,
        executions_filled INTEGER DEFAULT 0,
        ws_connected INTEGER DEFAULT 0
    )
    """

    @classmethod
    async def ensure_table(cls) -> None:
        async with await _get_db() as db:
            await db.execute(cls.TABLE)
            await db.commit()

    @classmethod
    async def insert(cls, **kwargs) -> None:
        await cls.ensure_table()
        async with await _get_db() as db:
            await db.execute(
                """INSERT INTO stats_history
                   (timestamp, hour, markets, price_updates, arbitrage_alerts,
                    executions_attempted, executions_filled, ws_connected)
                   VALUES (:timestamp, :hour, :markets, :price_updates, :arbitrage_alerts,
                           :executions_attempted, :executions_filled, :ws_connected)""",
                kwargs,
            )
            await db.commit()

    @classmethod
    async def get_recent(cls, limit: int = 48) -> list[dict]:
        await cls.ensure_table()
        async with await _get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM stats_history ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


class MinuteStatsRepository:
    """Stores per-minute price update stats."""

    TABLE = """
    CREATE TABLE IF NOT EXISTS minute_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        minute TEXT NOT NULL,
        price_updates INTEGER DEFAULT 0,
        ws_connected INTEGER DEFAULT 0
    )
    """

    @classmethod
    async def ensure_table(cls) -> None:
        async with await _get_db() as db:
            await db.execute(cls.TABLE)
            await db.commit()

    @classmethod
    async def insert(cls, **kwargs) -> None:
        await cls.ensure_table()
        async with await _get_db() as db:
            await db.execute(
                """INSERT INTO minute_stats (timestamp, minute, price_updates, ws_connected)
                   VALUES (:timestamp, :minute, :price_updates, :ws_connected)""",
                kwargs,
            )
            await db.commit()

    @classmethod
    async def get_recent(cls, limit: int = 60) -> list[dict]:
        await cls.ensure_table()
        async with await _get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM minute_stats ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


class NearMissAlertRepository:
    """Stores near-miss arbitrage alerts (illiquid or low balance)."""

    TABLE = """
    CREATE TABLE IF NOT EXISTS near_miss_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        market TEXT NOT NULL,
        yes_ask REAL,
        no_ask REAL,
        combined REAL,
        profit_pct REAL,
        yes_liquidity REAL,
        no_liquidity REAL,
        min_required REAL,
        reason TEXT
    )
    """

    @classmethod
    async def ensure_table(cls) -> None:
        async with await _get_db() as db:
            await db.execute(cls.TABLE)
            await db.commit()

    @classmethod
    async def insert(cls, **kwargs) -> None:
        await cls.ensure_table()
        async with await _get_db() as db:
            await db.execute(
                """INSERT INTO near_miss_alerts
                   (timestamp, market, yes_ask, no_ask, combined, profit_pct,
                    yes_liquidity, no_liquidity, min_required, reason)
                   VALUES (:timestamp, :market, :yes_ask, :no_ask, :combined, :profit_pct,
                           :yes_liquidity, :no_liquidity, :min_required, :reason)""",
                kwargs,
            )
            await db.commit()

    @classmethod
    async def get_recent(cls, limit: int = 50) -> list[dict]:
        await cls.ensure_table()
        async with await _get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM near_miss_alerts ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
