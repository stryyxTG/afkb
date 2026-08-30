from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

import telethon
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl import functions
from telethon.tl.types import User

from tglol.proxy_utils import mask_proxy, proxy_to_telethon
from tglol.session_compat import telethon_compatible_session_path


CODE_RE = re.compile(r"(?<!\d)(\d[\d\s-]{2,14}\d)(?!\d)")
CODE_CONTEXT_RE = re.compile(r"\b(code|otp|passcode|парол|код|подтверж)\b", re.IGNORECASE)
VERIFICATION_CODE_PEERS = ("VerificationCodes", "@VerificationCodes")
VERIFICATION_CODE_DIALOG_NAMES = ("Verification Codes", "VerificationCodes")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeRequest:
    phone_code_hash: str | None
    delivery_type: str
    next_type: str | None
    timeout: int | None
    code_length: int | None
    already_authorized: bool = False
    user: User | None = None


@dataclass(frozen=True)
class TelegramCodeMessage:
    code: str
    key: str
    date: datetime | None


def client_for(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    receive_updates: bool = False,
) -> TelegramClient:
    proxy = runtime.get("proxy")
    proxy_kwargs = proxy_to_telethon(proxy)
    proxy_tuple = proxy_kwargs.get("proxy")
    if proxy:
        proxy_len = len(proxy_tuple) if isinstance(proxy_tuple, tuple) else "mtproto" if proxy_tuple else 0
        logger.info(
            "Using Telegram proxy [%s]: %s tuple_len=%s telethon=%s",
            runtime.get("proxy_source") or "unknown",
            mask_proxy(proxy),
            proxy_len,
            telethon.__version__,
        )
    else:
        logger.info("Using Telegram proxy: none telethon=%s", telethon.__version__)
    return TelegramClient(
        str(telethon_compatible_session_path(session_path)),
        api_id,
        api_hash,
        device_model=runtime.get("device") or "Desktop",
        system_version=runtime.get("sdk") or "Windows 11 x64",
        app_version=runtime.get("app_version") or "6.9.3 x64",
        lang_code="en",
        system_lang_code="en-US",
        receive_updates=receive_updates,
        connection_retries=6,
        request_retries=6,
        retry_delay=1,
        timeout=12,
        auto_reconnect=False,
        **proxy_kwargs,
    )


async def send_code(
    session_path: Path,
    phone: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> CodeRequest:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            logger.info("Telegram session is already authorized: phone=%s", phone)
            return CodeRequest(
                phone_code_hash=None,
                delivery_type="Authorized",
                next_type=None,
                timeout=None,
                code_length=None,
                already_authorized=True,
                user=me,
            )

        sent = await client.send_code_request(phone)
        logger.info(
            "Telegram login code requested: phone=%s delivery=%s next=%s timeout=%s length=%s",
            phone,
            type(sent.type).__name__,
            type(sent.next_type).__name__ if sent.next_type else None,
            sent.timeout,
            getattr(sent.type, "length", None),
        )
        return CodeRequest(
            phone_code_hash=sent.phone_code_hash,
            delivery_type=type(sent.type).__name__,
            next_type=type(sent.next_type).__name__ if sent.next_type else None,
            timeout=sent.timeout,
            code_length=getattr(sent.type, "length", None),
        )
    finally:
        await client.disconnect()


async def sign_in_code(
    session_path: Path,
    phone: str,
    code: str,
    phone_code_hash: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> User:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        if me is None:
            raise RuntimeError("Login succeeded but account info is empty")
        return me
    finally:
        await client.disconnect()


async def sign_in_password(
    session_path: Path,
    password: str,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> User:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        if me is None:
            raise RuntimeError("Login succeeded but account info is empty")
        return me
    finally:
        await client.disconnect()


async def inspect_session(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> tuple[str, User | None, str | None]:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return "unauthorized", None, None
        me = await client.get_me()
        if me is None:
            return "empty", None, None
        return "active", me, None
    except SessionPasswordNeededError:
        return "twofa_required", None, None
    except Exception as exc:
        return "error", None, str(exc)
    finally:
        await client.disconnect()


def user_fields(user: User | None) -> dict[str, Any]:
    if user is None:
        return {
            "phone": None,
            "telegram_user_id": None,
            "username": None,
            "first_name": None,
            "last_name": None,
        }
    return {
        "phone": user.phone,
        "telegram_user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def extract_login_codes(text: str, *, require_context: bool = True) -> list[str]:
    candidates: list[str] = []
    for match in CODE_RE.finditer(text or ""):
        code = re.sub(r"\D+", "", match.group(1))
        if 4 <= len(code) <= 8:
            candidates.append(code)
    if not candidates:
        return []
    if not require_context or CODE_CONTEXT_RE.search(text or ""):
        return candidates
    return []


def _dialog_name_key(value: str | None) -> str:
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE).casefold()


def _entity_key(entity) -> tuple[str, int | str]:
    return type(entity).__name__, getattr(entity, "id", repr(entity))


async def _set_online(client: TelegramClient) -> None:
    try:
        await client(functions.account.UpdateStatusRequest(offline=False))
    except Exception as exc:
        logger.info("Cannot set account online: %s", exc)


async def _resolve_code_entities(
    client: TelegramClient,
    *,
    peers: tuple,
    dialog_names: tuple[str, ...] = (),
) -> list:
    entities: list = []
    seen: set[tuple[str, int | str]] = set()

    def add_entity(entity) -> None:
        key = _entity_key(entity)
        if key in seen:
            return
        seen.add(key)
        entities.append(entity)

    for peer in peers:
        try:
            add_entity(await client.get_entity(peer))
        except Exception as exc:
            logger.info("Cannot resolve code peer %s: %s", peer, exc)

    target_names = {_dialog_name_key(name) for name in dialog_names}
    if target_names:
        async for dialog in client.iter_dialogs(limit=100):
            entity = dialog.entity
            names = {
                _dialog_name_key(dialog.name),
                _dialog_name_key(getattr(entity, "username", None)),
            }
            if names & target_names:
                add_entity(entity)

    return entities


async def prepare_account_for_giveout(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    dialog_limit: int = 12,
) -> None:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorized")

        await client.get_me()
        await _set_online(client)

        try:
            async for _dialog in client.iter_dialogs(limit=dialog_limit):
                pass
        except Exception as exc:
            logger.info("Cannot read dialogs during giveout preflight: %s", exc)

        try:
            await client(functions.contacts.GetContactsRequest(hash=0))
        except Exception as exc:
            logger.info("Cannot read contacts during giveout preflight: %s", exc)

        try:
            await client(functions.contacts.GetBlockedRequest(offset=0, limit=20))
        except Exception as exc:
            logger.info("Cannot read blocked users during giveout preflight: %s", exc)
    finally:
        await client.disconnect()


async def send_user_message(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> int | None:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorized")
        await _set_online(client)
        message = await client.send_message(
            chat_id,
            text,
            reply_to=reply_to_message_id,
            parse_mode="html",
        )
        return getattr(message, "id", None)
    finally:
        await client.disconnect()

async def _get_recent_code_messages_from_peers(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    peers: tuple,
    dialog_names: tuple[str, ...] = (),
    limit: int = 15,
    require_context: bool = True,
) -> list[TelegramCodeMessage]:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorized")

        await _set_online(client)

        result: list[TelegramCodeMessage] = []
        entities = await _resolve_code_entities(client, peers=peers, dialog_names=dialog_names)
        for entity in entities:
            entity_id = f"{type(entity).__name__}:{getattr(entity, 'id', repr(entity))}"
            try:
                async for message in client.iter_messages(entity, limit=limit):
                    text = message.message or ""
                    for index, code in enumerate(extract_login_codes(text, require_context=require_context)):
                        result.append(
                            TelegramCodeMessage(
                                code=code,
                                key=f"{entity_id}:{getattr(message, 'id', '-')}: {index}",
                                date=message.date,
                            )
                        )
            except Exception as exc:
                logger.info("Cannot read login codes from peer %s: %s", entity, exc)
                continue
        return result
    finally:
        await client.disconnect()


async def _get_latest_code_from_peers(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    peers: tuple,
    dialog_names: tuple[str, ...] = (),
    limit: int = 15,
    require_context: bool = True,
) -> str | None:
    client = client_for(session_path, api_id, api_hash, runtime)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("session is not authorized")

        await _set_online(client)

        latest_code: str | None = None
        latest_date = None
        entities = await _resolve_code_entities(client, peers=peers, dialog_names=dialog_names)

        for entity in entities:
            try:
                async for message in client.iter_messages(entity, limit=limit):
                    text = message.message or ""
                    codes = extract_login_codes(text, require_context=require_context)
                    if codes:
                        if latest_date is None or (message.date and message.date > latest_date):
                            latest_code = codes[0]
                            latest_date = message.date
                        break
            except Exception as exc:
                logger.info("Cannot read login codes from peer %s: %s", entity, exc)
                continue
        return latest_code
    finally:
        await client.disconnect()


async def get_recent_telegram_codes(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    limit: int = 30,
) -> list[TelegramCodeMessage]:
    return await _get_recent_code_messages_from_peers(
        session_path,
        api_id,
        api_hash,
        runtime,
        peers=VERIFICATION_CODE_PEERS,
        dialog_names=VERIFICATION_CODE_DIALOG_NAMES,
        limit=limit,
    )


async def get_latest_telegram_code(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
    *,
    limit: int = 15,
) -> str | None:
    return await _get_latest_code_from_peers(
        session_path,
        api_id,
        api_hash,
        runtime,
        peers=VERIFICATION_CODE_PEERS,
        dialog_names=VERIFICATION_CODE_DIALOG_NAMES,
        limit=limit,
    )

