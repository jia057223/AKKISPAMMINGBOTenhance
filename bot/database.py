"""
Owner-scoped database layer.

Railway/PostgreSQL is the production target. SQLite remains available only for
local development when ENV is not production.
"""

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
from loguru import logger

import config

_DATABASE_URL: str = os.getenv("DATABASE_URL", "")
_ENV: str = os.getenv("ENV", os.getenv("RAILWAY_ENVIRONMENT", "development")).lower()
_IS_PRODUCTION: bool = _ENV in {"production", "prod"} or bool(os.getenv("RAILWAY_ENVIRONMENT_ID"))

if _IS_PRODUCTION and not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required in production/Railway. Attach a PostgreSQL service.")

_USE_PG: bool = bool(_DATABASE_URL)

if _USE_PG:
    import asyncpg as _asyncpg  # type: ignore[import]
else:
    _asyncpg = None  # type: ignore[assignment]

_pg_pool = None


async def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await _asyncpg.create_pool(_DATABASE_URL, min_size=1, max_size=5)
    return _pg_pool


class _Conn:
    def __init__(self, raw, is_pg: bool) -> None:
        self._raw = raw
        self.pg = is_pg

    @staticmethod
    def _pg_sql(sql: str, params: tuple) -> tuple[str, list]:
        n = 0

        def _sub(_m: re.Match) -> str:
            nonlocal n
            n += 1
            return f"${n}"

        return re.sub(r"\?", _sub, sql), list(params)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        if self.pg:
            q, p = self._pg_sql(sql, params)
            return [dict(r) for r in await self._raw.fetch(q, *p)]
        self._raw.row_factory = aiosqlite.Row
        async with self._raw.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        if self.pg:
            q, p = self._pg_sql(sql, params)
            row = await self._raw.fetchrow(q, *p)
            return dict(row) if row else None
        self._raw.row_factory = aiosqlite.Row
        async with self._raw.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchval(self, sql: str, params: tuple = ()) -> Any:
        if self.pg:
            q, p = self._pg_sql(sql, params)
            return await self._raw.fetchval(q, *p)
        async with self._raw.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def execute(self, sql: str, params: tuple = ()) -> None:
        if self.pg:
            q, p = self._pg_sql(sql, params)
            await self._raw.execute(q, *p)
        else:
            await self._raw.execute(sql, params)

    async def insert_id(self, sql: str, params: tuple = ()) -> int:
        if self.pg:
            q, p = self._pg_sql(sql if "RETURNING" in sql.upper() else sql + " RETURNING id", params)
            return int(await self._raw.fetchval(q, *p))
        async with self._raw.execute(sql, params) as cur:
            return int(cur.lastrowid)

    async def commit(self) -> None:
        if not self.pg:
            await self._raw.commit()


@asynccontextmanager
async def _db():
    if _USE_PG:
        pool = await _get_pg_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield _Conn(conn, True)
    else:
        async with aiosqlite.connect(config.DB_PATH) as conn:
            yield _Conn(conn, False)


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    key TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    session_str TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_user_id, phone)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_banned INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    plan_key TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    account_limit INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payment_file_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS user_broadcast_settings (
    owner_user_id INTEGER PRIMARY KEY,
    speed_preset TEXT NOT NULL DEFAULT 'medium',
    custom_batch_size TEXT NOT NULL DEFAULT '5',
    custom_batch_delay TEXT NOT NULL DEFAULT '5',
    custom_cycle_delay TEXT NOT NULL DEFAULT '30',
    selected_accounts TEXT NOT NULL DEFAULT 'all',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS broadcast_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    key TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS accounts (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    session_str TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_user_id, phone)
);
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_banned SMALLINT NOT NULL DEFAULT 0,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT,
    full_name TEXT,
    plan_key TEXT NOT NULL,
    plan_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    account_limit INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payment_file_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS user_broadcast_settings (
    owner_user_id BIGINT PRIMARY KEY,
    speed_preset TEXT NOT NULL DEFAULT 'medium',
    custom_batch_size TEXT NOT NULL DEFAULT '5',
    custom_batch_delay TEXT NOT NULL DEFAULT '5',
    custom_cycle_delay TEXT NOT NULL DEFAULT '30',
    selected_accounts TEXT NOT NULL DEFAULT 'all',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS broadcast_stats (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def init_db() -> None:
    if _USE_PG:
        pool = await _get_pg_pool()
        async with pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            applied = await conn.fetchval("SELECT key FROM schema_migrations WHERE key = 'owner_scope_clean'")
            if not applied:
                await conn.execute("DROP TABLE IF EXISTS accounts CASCADE")
                await conn.execute("DROP TABLE IF EXISTS messages CASCADE")
            for stmt in _PG_SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(stmt)
            if not applied:
                await conn.execute("INSERT INTO schema_migrations (key) VALUES ('owner_scope_clean') ON CONFLICT(key) DO NOTHING")
        logger.info("PostgreSQL database initialised with clean owner-scoped tables")
    else:
        async with aiosqlite.connect(config.DB_PATH) as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            cur = await conn.execute("SELECT key FROM schema_migrations WHERE key = 'owner_scope_clean'")
            applied = await cur.fetchone()
            if not applied:
                await conn.execute("DROP TABLE IF EXISTS accounts")
                await conn.execute("DROP TABLE IF EXISTS messages")
            await conn.executescript(_SQLITE_SCHEMA)
            if not applied:
                await conn.execute("INSERT OR IGNORE INTO schema_migrations (key, applied_at) VALUES ('owner_scope_clean', datetime('now'))")
            await conn.commit()
        logger.warning("SQLite database initialised for local development only at {}", config.DB_PATH)


async def get_accounts(owner_user_id: int) -> list[dict]:
    async with _db() as c:
        return await c.fetchall("SELECT * FROM accounts WHERE owner_user_id = ? ORDER BY id", (owner_user_id,))


async def get_all_accounts_for_admin() -> list[dict]:
    async with _db() as c:
        return await c.fetchall("SELECT * FROM accounts ORDER BY owner_user_id, id")


async def count_user_accounts(owner_user_id: int) -> int:
    async with _db() as c:
        return int(await c.fetchval("SELECT COUNT(*) FROM accounts WHERE owner_user_id = ?", (owner_user_id,)) or 0)


async def add_account(owner_user_id: int, name: str, phone: str, session_str: str) -> int:
    async with _db() as c:
        await c.execute("""
            INSERT INTO accounts (owner_user_id, name, phone, session_str, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(owner_user_id, phone) DO UPDATE SET
                name = excluded.name,
                session_str = excluded.session_str,
                status = 'active'
        """, (owner_user_id, name, phone, session_str))
        await c.commit()
        return int(await c.fetchval(
            "SELECT id FROM accounts WHERE owner_user_id = ? AND phone = ?",
            (owner_user_id, phone),
        ))


async def remove_account(owner_user_id: int, account_id: int) -> dict | None:
    async with _db() as c:
        row = await c.fetchone("SELECT * FROM accounts WHERE owner_user_id = ? AND id = ?", (owner_user_id, account_id))
        if not row:
            return None
        await c.execute("DELETE FROM accounts WHERE owner_user_id = ? AND id = ?", (owner_user_id, account_id))
        await c.commit()
    return row


async def set_account_status(owner_user_id: int, account_id: int, status: str) -> None:
    async with _db() as c:
        await c.execute(
            "UPDATE accounts SET status = ? WHERE owner_user_id = ? AND id = ?",
            (status, owner_user_id, account_id),
        )
        await c.commit()


async def get_messages(owner_user_id: int) -> list[dict]:
    async with _db() as c:
        return await c.fetchall("SELECT * FROM messages WHERE owner_user_id = ? ORDER BY id", (owner_user_id,))


async def get_all_messages_for_admin() -> list[dict]:
    async with _db() as c:
        return await c.fetchall("SELECT * FROM messages ORDER BY owner_user_id, id")


async def add_message(owner_user_id: int, text: str) -> int:
    async with _db() as c:
        msg_id = await c.insert_id("INSERT INTO messages (owner_user_id, text) VALUES (?, ?)", (owner_user_id, text))
        await c.commit()
    return msg_id


async def remove_message(owner_user_id: int, msg_id: int) -> bool:
    async with _db() as c:
        exists = await c.fetchval("SELECT id FROM messages WHERE owner_user_id = ? AND id = ?", (owner_user_id, msg_id))
        if not exists:
            return False
        await c.execute("DELETE FROM messages WHERE owner_user_id = ? AND id = ?", (owner_user_id, msg_id))
        await c.commit()
    return True


async def get_setting(key: str, default: str = "") -> str:
    async with _db() as c:
        val = await c.fetchval("SELECT value FROM settings WHERE key = ?", (key,))
    return val if val is not None else default


async def set_setting(key: str, value: str) -> None:
    async with _db() as c:
        await c.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))
        await c.commit()


async def get_user_broadcast_setting(owner_user_id: int, key: str, default: str = "") -> str:
    await ensure_user_broadcast_settings(owner_user_id)
    async with _db() as c:
        val = await c.fetchval(f"SELECT {key} FROM user_broadcast_settings WHERE owner_user_id = ?", (owner_user_id,))
    return val if val is not None else default


async def set_user_broadcast_setting(owner_user_id: int, key: str, value: str) -> None:
    allowed = {
        "speed_preset",
        "custom_batch_size",
        "custom_batch_delay",
        "custom_cycle_delay",
        "selected_accounts",
    }

    if key not in allowed:
        raise ValueError(f"Invalid broadcast setting: {key}")

    await ensure_user_broadcast_settings(owner_user_id)

    async with _db() as c:
        await c.execute(
            f"UPDATE user_broadcast_settings SET {key} = ?, updated_at = ? WHERE owner_user_id = ?",
            (value, datetime.utcnow(), owner_user_id)
        )
        await c.commit()


async def ensure_user_broadcast_settings(owner_user_id: int) -> None:
    async with _db() as c:
        await c.execute("""
            INSERT INTO user_broadcast_settings (owner_user_id)
            VALUES (?)
            ON CONFLICT(owner_user_id) DO NOTHING
        """, (owner_user_id,))
        await c.commit()


async def record_broadcast_result(owner_user_id: int, sent: int, failed: int) -> None:
    async with _db() as c:
        await c.execute("INSERT INTO broadcast_stats (owner_user_id, sent, failed) VALUES (?, ?, ?)", (owner_user_id, sent, failed))
        await c.commit()


async def get_owner_statistics(owner_user_id: int) -> dict:
    async with _db() as c:
        accounts = int(await c.fetchval("SELECT COUNT(*) FROM accounts WHERE owner_user_id = ?", (owner_user_id,)) or 0)
        messages = int(await c.fetchval("SELECT COUNT(*) FROM messages WHERE owner_user_id = ?", (owner_user_id,)) or 0)
        total_sent = int(await c.fetchval("SELECT COALESCE(SUM(sent), 0) FROM broadcast_stats WHERE owner_user_id = ?", (owner_user_id,)) or 0)
        total_failed = int(await c.fetchval("SELECT COALESCE(SUM(failed), 0) FROM broadcast_stats WHERE owner_user_id = ?", (owner_user_id,)) or 0)
        today = datetime.utcnow().date().isoformat()
        week = (datetime.utcnow() - timedelta(days=7)).isoformat()
        month = (datetime.utcnow() - timedelta(days=30)).isoformat()
        today_sent = int(await c.fetchval("SELECT COALESCE(SUM(sent), 0) FROM broadcast_stats WHERE owner_user_id = ? AND created_at >= ?", (owner_user_id, today)) or 0)
        weekly_sent = int(await c.fetchval("SELECT COALESCE(SUM(sent), 0) FROM broadcast_stats WHERE owner_user_id = ? AND created_at >= ?", (owner_user_id, week)) or 0)
        monthly_sent = int(await c.fetchval("SELECT COALESCE(SUM(sent), 0) FROM broadcast_stats WHERE owner_user_id = ? AND created_at >= ?", (owner_user_id, month)) or 0)
    attempts = total_sent + total_failed
    return {
        "accounts": accounts,
        "messages": messages,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "today_sent": today_sent,
        "weekly_sent": weekly_sent,
        "monthly_sent": monthly_sent,
        "success_rate": int(total_sent / attempts * 100) if attempts else 0,
    }


async def upsert_user(user_id: int, username: str | None, full_name: str) -> None:
    now = datetime.utcnow().isoformat()
    async with _db() as c:
        await c.execute("""
            INSERT INTO users (user_id, username, full_name, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen = excluded.last_seen
        """, (user_id, username, full_name, now))
        await c.commit()
    await ensure_user_broadcast_settings(user_id)


async def get_user(user_id: int) -> dict | None:
    async with _db() as c:
        return await c.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))


async def get_all_users() -> list[dict]:
    async with _db() as c:
        return await c.fetchall("SELECT * FROM users ORDER BY last_seen DESC")


async def get_user_count() -> int:
    async with _db() as c:
        return int(await c.fetchval("SELECT COUNT(*) FROM users") or 0)


async def is_banned(user_id: int) -> bool:
    async with _db() as c:
        return bool(await c.fetchval("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)))


async def ban_user(user_id: int) -> None:
    async with _db() as c:
        await c.execute("""
            INSERT INTO users (user_id, is_banned) VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET is_banned = 1
        """, (user_id,))
        await c.commit()


async def unban_user(user_id: int) -> bool:
    async with _db() as c:
        exists = await c.fetchval("SELECT user_id FROM users WHERE user_id = ? AND is_banned = 1", (user_id,))
        if not exists:
            return False
        await c.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        await c.commit()
    return True


async def create_subscription(user_id: int, username: str, full_name: str, plan_key: str, plan_name: str, price: int, account_limit: int, payment_file_id: str | None = None) -> int:
    async with _db() as c:
        sub_id = await c.insert_id("""
            INSERT INTO subscriptions
                (user_id, username, full_name, plan_key, plan_name, price, account_limit, payment_file_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (user_id, username, full_name, plan_key, plan_name, price, account_limit, payment_file_id))
        await c.commit()
    return sub_id


async def get_subscriptions(status: str | None = None) -> list[dict]:
    async with _db() as c:
        if status:
            return await c.fetchall("SELECT * FROM subscriptions WHERE status = ? ORDER BY created_at DESC", (status,))
        return await c.fetchall("SELECT * FROM subscriptions ORDER BY created_at DESC")


async def get_user_subscription(user_id: int) -> dict | None:
    now = datetime.utcnow().isoformat()
    async with _db() as c:
        return await c.fetchone("""
            SELECT * FROM subscriptions
            WHERE user_id = ? AND status = 'approved'
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY expires_at DESC LIMIT 1
        """, (user_id, now))


async def approve_subscription(sub_id: int, duration_days: int = 7) -> dict | None:
    async with _db() as c:
        row = await c.fetchone("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
        if not row:
            return None
        expires_at = (datetime.utcnow() + timedelta(days=duration_days)).isoformat()
        await c.execute("UPDATE subscriptions SET status = 'approved', expires_at = ? WHERE id = ?", (expires_at, sub_id))
        await c.commit()
    row["expires_at"] = expires_at
    row["status"] = "approved"
    return row


async def reject_subscription(sub_id: int) -> dict | None:
    async with _db() as c:
        row = await c.fetchone("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
        if not row:
            return None
        await c.execute("UPDATE subscriptions SET status = 'rejected' WHERE id = ?", (sub_id,))
        await c.commit()
    return row


async def get_sub_counts() -> dict:
    async with _db() as c:
        total = await c.fetchval("SELECT COUNT(*) FROM subscriptions") or 0
        pending = await c.fetchval("SELECT COUNT(*) FROM subscriptions WHERE status = 'pending'") or 0
        approved = await c.fetchval("SELECT COUNT(*) FROM subscriptions WHERE status = 'approved'") or 0
        rejected = await c.fetchval("SELECT COUNT(*) FROM subscriptions WHERE status = 'rejected'") or 0
    return {"total": int(total), "pending": int(pending), "approved": int(approved), "rejected": int(rejected)}
