from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

import telethon
from telethon import TelegramClient, functions, types
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError, RPCError, SessionPasswordNeededError, UserDeactivatedBanError, UserDeactivatedError
from telethon.tl.types import User

from tglol.proxy_utils import mask_proxy, proxy_to_telethon
from tglol.session_compat import telethon_compatible_session_path


CODE_RE = re.compile(r"(?<!\d)(\d[\d\s-]{2,14}\d)(?!\d)")
CODE_CONTEXT_RE = re.compile(r"\b(code|otp|passcode|парол|код|подтверж)\b", re.IGNORECASE)
TELEGRAM_CODE_PEERS = (777000, "Telegram")
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
        receive_updates=False,
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


def _decode_telegram_json(value: Any) -> Any:
    if isinstance(value, types.JsonObject):
        return {item.key: _decode_telegram_json(item.value) for item in value.value}
    if isinstance(value, types.JsonArray):
        return [_decode_telegram_json(item) for item in value.value]
    if isinstance(value, (types.JsonString, types.JsonNumber, types.JsonBool)):
        return value.value
    if isinstance(value, types.JsonNull):
        return None
    return value


def _frozen_result(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "frozen",
        "reason": reason,
        **extra,
    }


async def check_account_app_config_freeze(client: TelegramClient) -> dict[str, Any]:
    try:
        result = await client(functions.help.GetAppConfigRequest(hash=0))
    except Exception as exc:
        return _telegram_valid_check_error_result(exc)

    if isinstance(result, types.help.AppConfigNotModified):
        return {"ok": True, "status": "unknown", "reason": "app_config_not_modified"}

    config = _decode_telegram_json(getattr(result, "config", None))
    if not isinstance(config, dict):
        return {"ok": True, "status": "unknown", "reason": "app_config_empty"}

    frozen_since = config.get("freeze_since_date") or 0
    if frozen_since:
        return _frozen_result(
            "app_config_freeze",
            freeze_since=frozen_since,
            freeze_until=config.get("freeze_until_date"),
            appeal_url=config.get("freeze_appeal_url"),
        )

    return {"ok": True, "status": "active", "reason": "app_config_active"}


def _telegram_valid_check_error_result(exc: Exception) -> dict[str, Any]:
    message = str(getattr(exc, "message", "") or str(exc) or "")
    if message in {"FROZEN_METHOD_INVALID", "FROZEN_PARTICIPANT_MISSING"}:
        return _frozen_result(message.lower(), rpc_error=message)
    if message in {"AUTH_KEY_UNREGISTERED", "SESSION_REVOKED"}:
        return {
            "ok": False,
            "status": "dead",
            "reason": message.lower(),
        }
    if message in {"USER_DEACTIVATED", "USER_DEACTIVATED_BAN"}:
        reason = "user_deactivated_ban" if message == "USER_DEACTIVATED_BAN" else "user_deactivated"
        return _frozen_result(reason, rpc_error=message)
    if isinstance(exc, (UserDeactivatedBanError, UserDeactivatedError)):
        reason = "user_deactivated_ban" if isinstance(exc, UserDeactivatedBanError) else "user_deactivated"
        return _frozen_result(reason)
    if isinstance(exc, AuthKeyUnregisteredError):
        return {
            "ok": False,
            "status": "dead",
            "reason": "auth_key_unregistered",
        }
    if isinstance(exc, SessionPasswordNeededError):
        return {
            "ok": False,
            "status": "need_2fa",
            "reason": "session_password_needed",
        }
    if isinstance(exc, FloodWaitError):
        return {
            "ok": False,
            "status": "skipped",
            "reason": f"flood_wait_{exc.seconds}",
        }
    if isinstance(exc, RPCError):
        return {
            "ok": False,
            "status": "error",
            "reason": type(exc).__name__,
        }
    return {
        "ok": False,
        "status": "error",
        "reason": type(exc).__name__,
    }


async def check_account_freeze(client: TelegramClient) -> dict[str, Any]:
    try:
        me = await client.get_me()
        if not me:
            return {
                "ok": False,
                "status": "dead",
                "reason": "empty_me",
            }

        if getattr(me, "deleted", False):
            return _frozen_result("user_deleted_flag", user=me)
        if getattr(me, "restricted", False) or getattr(me, "restriction_reason", None):
            return _frozen_result("user_restricted_flag", user=me)

        app_config_result = await check_account_app_config_freeze(client)
        if app_config_result["status"] == "frozen":
            return {**app_config_result, "user": me}

        await client.get_dialogs(limit=1)
        await client(functions.updates.GetStateRequest())
        await client(functions.account.GetAuthorizationsRequest())
        return {
            "ok": True,
            "status": "alive",
            "reason": "ok",
            "user": me,
            "user_id": me.id,
            "phone": getattr(me, "phone", None),
            "username": getattr(me, "username", None),
            "first_name": getattr(me, "first_name", None),
            "last_name": getattr(me, "last_name", None),
        }
    except Exception as exc:
        return _telegram_valid_check_error_result(exc)


async def inspect_session_with_freeze_check(
    session_path: Path,
    api_id: int,
    api_hash: str,
    runtime: dict[str, str],
) -> dict[str, Any]:
    client = client_for(session_path, api_id, api_hash, runtime)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {
                "ok": False,
                "status": "dead",
                "account_status": "unauthorized",
                "reason": "unauthorized",
                "user": None,
            }
        result = await check_account_freeze(client)
    except Exception as exc:
        result = _telegram_valid_check_error_result(exc)
    finally:
        with suppress(Exception):
            await client.disconnect()

    status = str(result.get("status") or "error")
    account_status = "active" if status == "alive" else status
    user = result.get("user") if status == "alive" else None
    return {
        "ok": bool(result.get("ok")),
        "status": status,
        "account_status": account_status,
        "reason": str(result.get("reason") or account_status),
        "user": user,
        **{key: value for key, value in result.items() if key not in {"ok", "status", "reason", "user"}},
    }

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

    target_ids = {peer for peer in peers if isinstance(peer, int)}
    target_names = {_dialog_name_key(name) for name in dialog_names}
    if target_ids or target_names:
        async for dialog in client.iter_dialogs(limit=100):
            entity = dialog.entity
            names = {
                _dialog_name_key(dialog.name),
                _dialog_name_key(getattr(entity, "username", None)),
            }
            if getattr(entity, "id", None) in target_ids or names & target_names:
                add_entity(entity)

    return entities


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
        peers=TELEGRAM_CODE_PEERS,
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
        peers=TELEGRAM_CODE_PEERS,
        limit=limit,
    )

