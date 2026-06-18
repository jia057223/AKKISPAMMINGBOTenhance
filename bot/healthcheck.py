"""Deployment health check for Railway worker environments."""

import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    required = ["BOT_TOKEN", "API_ID", "API_HASH", "ADMIN_IDS", "DATABASE_URL"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print(f"missing required env: {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        await conn.execute("SELECT 1")
        await conn.close()
    except Exception as exc:
        print(f"database check failed: {exc}", file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
