"""
Manages Telethon client sessions for multiple Telegram accounts.
Supports login via phone + OTP (+ optional 2FA) and session-string import.
"""

import asyncio
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

import config
import database as db
from loguru import logger

# In-memory store of active Telethon clients {account_id: TelegramClient}
_clients: dict[int, TelegramClient] = {}

# Temporary state for multi-step /add flow per chat
_add_state: dict[int, dict[str, Any]] = {}


# ─── Environment Validation Helper ───────────────────────────────────────────

def _verify_runtime_environment() -> None:
    """Intercepts and prints variable byte-representations to the Railway logs."""
    print(f"DEBUG INTERCEPT - API_ID: {repr(config.API_ID)} | Type: {type(config.API_ID)}", flush=True)
    print(f"DEBUG INTERCEPT - API_HASH: {repr(config.API_HASH)} | Type: {type(config.API_HASH)}", flush=True)
    
    assert config.API_ID != 0, "FATAL: API_ID is 0. Railway env vars are detached or falling back to default."
    assert config.API_HASH != "", "FATAL: API_HASH is empty. Railway env vars are detached or falling back to default."


# ─── Client lifecycle ─────────────────────────────────────────────────────────

async def load_all_clients(owner_user_id: int | None = None) -> None:
    accounts = await db.get_all_accounts_for_admin() if owner_user_id is None else await db.get_accounts(owner_user_id)
    for acc in accounts:
        await _start_client_from_string(acc["owner_user_id"], acc["id"], acc["session_str"], acc["phone"])


async def _start_client_from_string(
    owner_user_id: int, account_id: int, session_str: str, phone: str
) -> TelegramClient | None:
    try:
        _verify_runtime_environment()
        client = TelegramClient(StringSession(session_str), config.API_ID, config.API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("Account {} is not authorised; marking offline", phone)
            await db.set_account_status(owner_user_id, account_id, "offline")
            await client.disconnect()
            return None
        _clients[account_id] = client
        await db.set_account_status(owner_user_id, account_id, "active")
        logger.info("Client loaded for account_id={} phone={}", account_id, phone)
        return client
    except Exception as exc:
        logger.error("Failed to start client for {}: {}", phone, exc)
        return None


async def get_client(owner_user_id: int, account_id: int) -> TelegramClient | None:
    owned_ids = {a["id"] for a in await db.get_accounts(owner_user_id)}
    if account_id not in owned_ids:
        return None
    return _clients.get(account_id)


async def get_all_active_clients(owner_user_id: int | None = None) -> list[tuple[int, TelegramClient]]:
    if owner_user_id is None:
        raise ValueError("owner_user_id is required to access active clients")
    owned_ids = {a["id"] for a in await db.get_accounts(owner_user_id)}
    return [(aid, c) for aid, c in _clients.items() if aid in owned_ids and c.is_connected()]


async def disconnect_all() -> None:
    for client in _clients.values():
        try:
            await client.disconnect()
        except Exception:
            pass
    _clients.clear()


# ─── Session-string import ────────────────────────────────────────────────────

async def import_session_string(owner_user_id: int, session_string: str) -> dict:
    _verify_runtime_environment()
    client = TelegramClient(StringSession(session_string), config.API_ID, config.API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise ValueError("Session string is invalid or expired.")

    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    phone = me.phone or "unknown"

    account_id = await db.add_account(owner_user_id, name, phone, session_string)

    _clients[account_id] = client
    logger.info("Imported session for {} ({})", name, phone)
    return {"id": account_id, "name": name, "phone": phone}


# ─── Phone + OTP login flow ───────────────────────────────────────────────────

async def begin_phone_login(chat_id: int, owner_user_id: int, phone: str) -> None:
    _verify_runtime_environment()
    client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
    await client.connect()
    result = await client.send_code_request(phone)
    _add_state[chat_id] = {
        "client": client,
        "owner_user_id": owner_user_id,
        "phone": phone,
        "phone_code_hash": result.phone_code_hash,
        "step": "otp",
    }
    logger.info("OTP sent to {}", phone)


async def submit_otp(chat_id: int, otp: str) -> dict | None:
    state = _add_state.get(chat_id)
    if not state:
        raise ValueError("No active login session.")

    client: TelegramClient = state["client"]
    try:
        await client.sign_in(
            phone=state["phone"],
            code=otp,
            phone_code_hash=state["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        state["step"] = "password"
        return None
    except PhoneCodeInvalidError:
        raise ValueError("Invalid OTP code. Please try again.")

    return await _finalise_login(chat_id, client)


async def submit_password(chat_id: int, password: str) -> dict:
    state = _add_state.get(chat_id)
    if not state:
        raise ValueError("No active login session.")
    client: TelegramClient = state["client"]
    await client.sign_in(password=password)
    return await _finalise_login(chat_id, client)


async def _finalise_login(chat_id: int, client: TelegramClient) -> dict:
    state = _add_state.pop(chat_id, {})
    phone: str = state.get("phone", "unknown")

    me = await client.get_me()
    name = f"{me.first_name or ''} {me.last_name or ''}".strip()
    real_phone = me.phone or phone

    session_string = client.session.save()
    owner_user_id = int(state["owner_user_id"])
    account_id = await db.add_account(owner_user_id, name, real_phone, session_string)
    _clients[account_id] = client
    await db.set_account_status(owner_user_id, account_id, "active")

    logger.info("Login finalised for {} ({})", name, real_phone)
    return {"id": account_id, "name": name, "phone": real_phone}


async def cancel_add(chat_id: int) -> None:
    state = _add_state.pop(chat_id, None)
    if state and "client" in state:
        try:
            await state["client"].disconnect()
        except Exception:
            pass


def get_add_step(chat_id: int) -> str | None:
    return _add_state.get(chat_id, {}).get("step")


# ─── Remove account ────────────────────────────────────────────────────────────

async def remove_account(owner_user_id: int, account_id: int) -> dict | None:
    row = await db.remove_account(owner_user_id, account_id)
    if not row:
        return None

    client = _clients.pop(account_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    logger.info("Removed account id={} owner={}", account_id, owner_user_id)
    return row


# ─── Group helpers ────────────────────────────────────────────────────────────

async def get_joined_groups(client: TelegramClient) -> list[Any]:
    groups = []
    try:
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                groups.append(dialog)
    except Exception as exc:
        logger.error("Error fetching dialogs: {}", exc)
    return groups
