from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from aiogram import Bot
from telethon import events

from tglol.config import Config
from tglol.db import get_code_receiver, update_code_receiver_status
from tglol.handlers import handle_code_receiver_trigger
from tglol.json_utils import load_json, pick_api, runtime_from_json
from tglol.telegram_service import client_for


logger = logging.getLogger(__name__)


def _receiver_key(receiver) -> tuple | None:
    if not receiver:
        return None
    return (
        receiver.session_path,
        receiver.json_effective_path or receiver.json_original_path,
    )


def _receiver_connection_params(receiver, config: Config):
    raw_path = receiver.json_original_path or receiver.json_effective_path
    if not raw_path:
        raise RuntimeError("code receiver JSON file is required")
    path = Path(raw_path)
    if not path.exists():
        raise RuntimeError(f"code receiver JSON file not found: {path}")
    data = load_json(path)
    api_id, api_hash = pick_api(data, config)
    runtime = runtime_from_json(data)
    if not runtime.get("proxy") and config.telegram_proxy:
        runtime["proxy"] = config.telegram_proxy
        runtime["proxy_source"] = "global"
    return api_id, api_hash, runtime


def _listener_session_path(session_path: Path) -> Path:
    target = session_path.with_name(f"{session_path.stem}_listener{session_path.suffix}")
    try:
        if not target.exists() or target.stat().st_mtime < session_path.stat().st_mtime:
            shutil.copy2(session_path, target)
    except OSError:
        return session_path
    return target


async def run_code_receiver_listener(bot: Bot, config: Config) -> None:
    client = None
    active_key = None
    while True:
        try:
            receiver = get_code_receiver(config)
            desired_key = _receiver_key(receiver)
            if not receiver or not config.trigger_chat_id:
                if client is not None:
                    await client.disconnect()
                    client = None
                    active_key = None
                await asyncio.sleep(10)
                continue

            if desired_key != active_key:
                if client is not None:
                    await client.disconnect()
                session_path = Path(receiver.session_path)
                if not session_path.exists():
                    update_code_receiver_status(config, "missing_file")
                    logger.warning("Code receiver session file is missing: %s", session_path)
                    client = None
                    active_key = desired_key
                    await asyncio.sleep(10)
                    continue
                api_id, api_hash, runtime = _receiver_connection_params(receiver, config)
                client = client_for(_listener_session_path(session_path), api_id, api_hash, runtime, receive_updates=True)

                @client.on(events.NewMessage(chats=config.trigger_chat_id))
                async def _on_trigger(event):
                    if getattr(event, "out", False):
                        return
                    try:
                        await handle_code_receiver_trigger(
                            bot,
                            config,
                            chat_id=int(event.chat_id),
                            requester_user_id=int(event.sender_id or 0),
                            message_id=int(event.id),
                            text=event.raw_text or "",
                            reply_to_message_id=int(event.reply_to_msg_id) if event.reply_to_msg_id else None,
                        )
                    except Exception as exc:
                        logger.exception("Code receiver trigger failed: %s", exc)

                await client.connect()
                if not await client.is_user_authorized():
                    update_code_receiver_status(config, "unauthorized")
                    await client.disconnect()
                    client = None
                    active_key = desired_key
                    await asyncio.sleep(10)
                    continue
                update_code_receiver_status(config, "active")
                active_key = desired_key
                logger.info("Code receiver listener started for %s", receiver.phone or receiver.username or receiver.telegram_user_id)

            await asyncio.sleep(10)
        except asyncio.CancelledError:
            if client is not None:
                await client.disconnect()
            raise
        except Exception as exc:
            logger.warning("Code receiver listener loop error: %s", exc)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None
                active_key = None
            await asyncio.sleep(10)
