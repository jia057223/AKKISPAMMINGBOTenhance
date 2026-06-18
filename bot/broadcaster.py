"""Per-owner broadcast sessions."""

import asyncio
import random
from typing import Awaitable, Callable

from loguru import logger

import account_manager as am
import config
import database as db

NotifyCallback = Callable[[int, str], Awaitable[None]]

_notify: NotifyCallback | None = None
_sessions: dict[int, "BroadcastSession"] = {}


def set_notify(callback: NotifyCallback) -> None:
    global _notify
    _notify = callback


def _empty_stats() -> dict:
    return {"cycles": 0, "sent": 0, "failed": 0, "dead_accounts": 0, "last_cycle_groups": 0}


class BroadcastSession:
    def __init__(self, owner_user_id: int) -> None:
        self.owner_user_id = owner_user_id
        self.task: asyncio.Task | None = None
        self.stats = _empty_stats()

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self) -> bool:
        if self.is_running():
            return False
        self.stats = _empty_stats()
        self.task = asyncio.create_task(self._broadcast_loop())
        await self._push("🚀 <b>Broadcast Started</b>")
        logger.info("Broadcast loop started for owner={}", self.owner_user_id)
        return True

    async def stop(self) -> bool:
        if not self.is_running():
            return False
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None
        await self._push(
            "⏹ <b>Broadcast Stopped</b>\n"
            f"Cycles {self.stats['cycles']} | ✅ {self.stats['sent']} | ❌ {self.stats['failed']}"
        )
        logger.info("Broadcast loop stopped for owner={}", self.owner_user_id)
        return True

    async def _push(self, text: str) -> None:
        if not _notify:
            return
        try:
            await _notify(self.owner_user_id, text)
        except Exception as exc:
            logger.warning("Failed to notify owner {}: {}", self.owner_user_id, exc)

    async def _resolve_speed(self) -> dict:
        preset_key = await db.get_user_broadcast_setting(self.owner_user_id, "speed_preset", "medium")
        preset = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
        if preset_key == "custom":
            batch_size_raw = await db.get_user_broadcast_setting(self.owner_user_id, "custom_batch_size", "5")
            batch_delay_raw = await db.get_user_broadcast_setting(self.owner_user_id, "custom_batch_delay", "5")
            cycle_delay_raw = await db.get_user_broadcast_setting(self.owner_user_id, "custom_cycle_delay", "30")
            try:
                batch_size: int | None = int(batch_size_raw) if batch_size_raw and int(batch_size_raw) > 0 else None
            except ValueError:
                batch_size = 5
            try:
                batch_delay = float(batch_delay_raw) if batch_delay_raw else 5.0
            except ValueError:
                batch_delay = 5.0
            try:
                cycle_delay = float(cycle_delay_raw) if cycle_delay_raw else 30.0
            except ValueError:
                cycle_delay = 30.0
            return {**preset, "batch_size": batch_size, "batch_delay": batch_delay, "cycle_delay": cycle_delay}
        return preset

    async def _get_selected_account_ids(self) -> list[int] | None:
        raw = await db.get_user_broadcast_setting(self.owner_user_id, "selected_accounts", "all")
        if not raw or raw == "all":
            return None
        try:
            return [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            return None

    async def _broadcast_loop(self) -> None:
        while True:
            try:
                await self._run_one_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Broadcast cycle error for owner={}: {}", self.owner_user_id, exc)
                await self._push(f"⚠ <b>Broadcast Error</b>\n{exc}")
                await asyncio.sleep(10)

    async def _run_one_cycle(self) -> None:
        messages = await db.get_messages(self.owner_user_id)
        if not messages:
            await self._push("⚠ No messages in your pool. Sleeping 60s.")
            await asyncio.sleep(60)
            return

        speed = await self._resolve_speed()
        selected_ids = await self._get_selected_account_ids()
        all_clients = await am.get_all_active_clients(self.owner_user_id)
        clients = [(aid, c) for aid, c in all_clients if selected_ids is None or aid in selected_ids]

        if not clients:
            await self._push("⚠ No active accounts available. Sleeping 60s.")
            await asyncio.sleep(60)
            return

        preset_key = await db.get_user_broadcast_setting(self.owner_user_id, "speed_preset", "medium")
        preset = config.SPEED_PRESETS.get(preset_key or "medium", config.SPEED_PRESETS["medium"])
        self.stats["cycles"] += 1
        cycle_num = self.stats["cycles"]
        account_results = []

        for account_id, client in clients:
            account_results.append(await self._send_for_account(account_id, client, messages, speed))

        cycle_sent = sum(r["sent"] for r in account_results)
        cycle_failed = sum(r["failed"] for r in account_results)
        cycle_total = cycle_sent + cycle_failed
        self.stats["sent"] += cycle_sent
        self.stats["failed"] += cycle_failed
        self.stats["last_cycle_groups"] = cycle_total
        await db.record_broadcast_result(self.owner_user_id, cycle_sent, cycle_failed)

        lines = [f"🏁 <b>Cycle {cycle_num}</b>\n"]
        for r in account_results:
            icon = "💀" if r["dead"] else "📡"
            dead_tag = " 💀 Dead" if r["dead"] else ""
            lines.append(f"{icon} <b>{r['name']}</b>{dead_tag}\n   ✅ {r['sent']}  ❌ {r['failed']}  📋 {r['sent'] + r['failed']} groups")
        lines.append(f"\n📊 <b>Total</b>: ✅ {cycle_sent}/{cycle_total} ❌ {cycle_failed}")
        if cycle_total:
            lines.append(f"📈 Success rate: {int(cycle_sent / cycle_total * 100)}%")

        cycle_sleep = _rand_delay(speed["cycle_delay"])
        lines.append(f"\n🔄 Next cycle in <b>~{int(cycle_sleep)}s</b>" if cycle_sleep > 0 else "\n🔄 Next cycle <b>immediately</b>")
        lines.append(f"⚡ Speed: {preset['label']} {preset['risk']}")
        await self._push("\n".join(lines))

        if cycle_sleep > 0:
            await asyncio.sleep(cycle_sleep)

    async def _send_for_account(self, account_id: int, client, messages: list[dict], speed: dict) -> dict:
        accounts_db = await db.get_accounts(self.owner_user_id)
        acc_info = next((a for a in accounts_db if a["id"] == account_id), {})
        name = acc_info.get("name", f"Account {account_id}")
        result = {"account_id": account_id, "name": name, "sent": 0, "failed": 0, "dead": False}

        groups = await am.get_joined_groups(client)
        if not groups:
            logger.warning("Account {} has no groups", account_id)
            return result

        random.shuffle(groups)
        self.stats["last_cycle_groups"] = len(groups)
        batch_size: int | None = speed["batch_size"]
        batch_delay = speed["batch_delay"]
        batches = [groups[i:i + batch_size] for i in range(0, len(groups), batch_size)] if batch_size else [groups]

        for batch_idx, batch in enumerate(batches):
            for group in batch:
                msg_text = random.choice(messages)["text"]
                try:
                    await client.send_message(group.entity, msg_text, parse_mode="html")
                    result["sent"] += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result["failed"] += 1
                    err = str(exc).lower()
                    if any(k in err for k in ("banned", "deactivated", "auth_key", "user_deactivated")):
                        await db.set_account_status(self.owner_user_id, account_id, "dead")
                        self.stats["dead_accounts"] += 1
                        result["dead"] = True
                        await self._push(f"💀 <b>Dead Account Alert</b>\n{name} was marked dead.")
                        return result
                    logger.warning("Account {} failed for group '{}': {}", account_id, getattr(group, "name", "?"), exc)
            if batch_size and batch_idx < len(batches) - 1:
                sleep_s = _rand_delay(batch_delay)
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
        return result


def _rand_delay(val) -> float:
    if val == 0:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return random.uniform(val[0], val[1])
    return 0.0


def get_session(owner_user_id: int) -> BroadcastSession:
    if owner_user_id not in _sessions:
        _sessions[owner_user_id] = BroadcastSession(owner_user_id)
    return _sessions[owner_user_id]


def get_stats(owner_user_id: int) -> dict:
    return get_session(owner_user_id).stats


def is_running(owner_user_id: int) -> bool:
    return get_session(owner_user_id).is_running()


async def start_broadcast(owner_user_id: int) -> bool:
    return await get_session(owner_user_id).start()


async def stop_broadcast(owner_user_id: int) -> bool:
    return await get_session(owner_user_id).stop()


async def stop_all() -> None:
    for session in list(_sessions.values()):
        if session.is_running():
            await session.stop()
