from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
import zipfile

from aiogram import Bot
from aiogram.types import Document

from tglol.config import Config
from tglol.db import add_account
from tglol.desktop_profile import utc_now_iso
from tglol.json_utils import json_identity, load_json, pick_api, pick_twofa, runtime_from_json
from tglol.paths import unique_path
from tglol.telegram_service import inspect_session, user_fields


SESSION_IMPORT_TIMEOUT = 35
ZipProgress = Callable[[int, int, str], Awaitable[None]]
TRANSIENT_IMPORT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "did not respond",
    "connect failed",
    "connection",
    "cannot connect",
    "disconnected",
    "network",
    "proxy",
    "temporar",
    "request retries",
    "server closed",
    "server disconnected",
    "winerror",
    "oserror",
    "clientoserror",
    "telegramnetworkerror",
)


class SessionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportResult:
    account_id: int
    status: str
    phone: str | None
    username: str | None
    source: str
    note: str | None = None


@dataclass(frozen=True)
class PendingRetry:
    result_index: int
    session_tmp: Path
    json_tmp: Path


async def download_document(bot: Bot, document: Document, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    await bot.download(document, destination=destination)
    return destination


async def import_session_account(
    config: Config,
    *,
    session_path: Path,
    source_type: str,
    created_by: int | None,
    json_path: Path | None = None,
    twofa_password: str | None = None,
) -> ImportResult:
    if json_path is None:
        raise ValueError("session import requires a matching JSON file")

    uploaded_json = load_json(json_path)
    api_id, api_hash = pick_api(uploaded_json, config)
    runtime = runtime_from_json(uploaded_json)
    if not runtime.get("proxy") and config.telegram_proxy:
        runtime["proxy"] = config.telegram_proxy
        runtime["proxy_source"] = "global"
    parsed_twofa = pick_twofa(uploaded_json)
    twofa_password = parsed_twofa if parsed_twofa is not None else twofa_password

    identity = json_identity(uploaded_json)
    fields = user_fields(None)
    for key, value in identity.items():
        if key == "session_file":
            continue
        if value not in (None, ""):
            fields[key] = value

    try:
        status, user, note = await asyncio.wait_for(
            inspect_session(session_path, api_id, api_hash, runtime),
            timeout=SESSION_IMPORT_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise SessionValidationError(f"Telegram did not respond in {SESSION_IMPORT_TIMEOUT} sec.") from exc
    except Exception as exc:
        raise SessionValidationError(f"Inspect failed: {exc}") from exc

    if status != "active":
        detail = f": {note}" if note else ""
        raise SessionValidationError(f"Session is not active: {status}{detail}")

    inspected_fields = user_fields(user)
    for key, value in inspected_fields.items():
        if value not in (None, ""):
            fields[key] = value

    now = utc_now_iso()
    account_id = add_account(
        config,
        {
            "phone": fields["phone"],
            "telegram_user_id": fields["telegram_user_id"],
            "username": fields["username"],
            "first_name": fields["first_name"],
            "last_name": fields["last_name"],
            "session_path": str(session_path),
            "json_original_path": str(json_path),
            "json_effective_path": str(json_path),
            "json_source": "uploaded",
            "twofa_password": twofa_password,
            "source_type": source_type,
            "status": status,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        },
    )
    return ImportResult(account_id, status, fields["phone"], fields["username"], source_type, note)


def normalize_match_key(value: str) -> str:
    return "".join(ch for ch in value.lower().strip() if ch.isalnum())


def normalize_phone(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = Path(member.filename).name
            if not name:
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in {".session", ".json"}:
                continue
            target = unique_path(destination, name)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def match_zip_files(session_files: list[Path], json_files: list[Path]) -> dict[Path, Path | None]:
    unmatched_jsons = set(json_files)
    result: dict[Path, Path | None] = {session: None for session in session_files}

    by_exact = {json_path.stem.lower(): json_path for json_path in json_files}
    for session in session_files:
        match = by_exact.get(session.stem.lower())
        if match and match in unmatched_jsons:
            result[session] = match
            unmatched_jsons.remove(match)

    by_normalized_name = {normalize_match_key(json_path.stem): json_path for json_path in unmatched_jsons}
    for session in session_files:
        if result[session]:
            continue
        match = by_normalized_name.get(normalize_match_key(session.stem))
        if match and match in unmatched_jsons:
            result[session] = match
            unmatched_jsons.remove(match)

    by_phone: dict[str, Path] = {}
    by_id: dict[str, Path] = {}
    by_session_file: dict[str, Path] = {}
    for json_path in list(unmatched_jsons):
        try:
            data = load_json(json_path)
        except Exception:
            continue
        phone = data.get("phone") or data.get("phone_number")
        user_id = data.get("user_id") or data.get("id")
        session_file = data.get("session_file")
        if phone:
            by_phone[normalize_phone(str(phone))] = json_path
        if user_id:
            by_id[normalize_match_key(str(user_id))] = json_path
        if session_file:
            by_session_file[Path(str(session_file)).stem.lower()] = json_path

    for session in session_files:
        if result[session]:
            continue
        candidates = [
            by_session_file.get(session.stem.lower()),
            by_phone.get(normalize_phone(session.stem)),
            by_id.get(normalize_match_key(session.stem)),
        ]
        for match in candidates:
            if match and match in unmatched_jsons:
                result[session] = match
                unmatched_jsons.remove(match)
                break

    unresolved_sessions = [session for session in session_files if result[session] is None]
    if len(unresolved_sessions) == 1 and len(unmatched_jsons) == 1:
        result[unresolved_sessions[0]] = next(iter(unmatched_jsons))
        unmatched_jsons.clear()
    elif len(unresolved_sessions) == len(unmatched_jsons) and unresolved_sessions:
        for session, json_path in zip(sorted(unresolved_sessions), sorted(unmatched_jsons)):
            result[session] = json_path
        unmatched_jsons.clear()

    return result


def _remove_copied_files(*paths: Path | None) -> None:
    for path in paths:
        if path is not None:
            Path(path).unlink(missing_ok=True)


def _is_retryable_import_error(exc: Exception) -> bool:
    if not isinstance(exc, SessionValidationError):
        return False
    message = str(exc).casefold()
    if "session is not active" in message:
        return False
    return any(marker in message for marker in TRANSIENT_IMPORT_ERROR_MARKERS)


async def _copy_and_import_zip_account(
    config: Config,
    *,
    session_tmp: Path,
    json_tmp: Path,
    created_by: int | None,
) -> ImportResult:
    final_session: Path | None = None
    final_json: Path | None = None
    try:
        final_session = unique_path(config.sessions_dir, session_tmp.name)
        shutil.copy2(session_tmp, final_session)
        final_json = unique_path(config.json_dir, json_tmp.name)
        shutil.copy2(json_tmp, final_json)
        return await import_session_account(
            config,
            session_path=final_session,
            json_path=final_json,
            source_type="zip",
            created_by=created_by,
        )
    except Exception:
        _remove_copied_files(final_session, final_json)
        raise


def _zip_error_result(session_tmp: Path, exc: Exception, *, retried: bool = False) -> ImportResult:
    prefix = "retry failed: " if retried else ""
    return ImportResult(
        account_id=0,
        status="error",
        phone=None,
        username=None,
        source="zip",
        note=f"{session_tmp.name}: {prefix}{exc}",
    )


async def import_zip(
    config: Config,
    *,
    zip_path: Path,
    created_by: int | None,
    progress: ZipProgress | None = None,
) -> tuple[list[ImportResult], str]:
    batch_dir = unique_path(config.temp_dir, zip_path.stem)
    batch_dir.mkdir(parents=True, exist_ok=True)
    safe_extract_zip(zip_path, batch_dir)

    session_files = sorted(batch_dir.glob("*.session"))
    json_files = sorted(batch_dir.glob("*.json"))
    matches = match_zip_files(session_files, json_files)

    results: list[ImportResult] = []
    retry_queue: list[PendingRetry] = []
    total = len(matches)
    if progress:
        await progress(0, total, "старт")
    for session_tmp, json_tmp in matches.items():
        try:
            if json_tmp is None:
                result = ImportResult(
                    account_id=0,
                    status="error",
                    phone=None,
                    username=None,
                    source="zip",
                    note=f"{session_tmp.name}: matching JSON file is required.",
                )
            else:
                result = await _copy_and_import_zip_account(
                    config,
                    session_tmp=session_tmp,
                    json_tmp=json_tmp,
                    created_by=created_by,
                )
        except Exception as exc:
            result = _zip_error_result(session_tmp, exc)
            if json_tmp is not None and _is_retryable_import_error(exc):
                retry_queue.append(PendingRetry(len(results), session_tmp, json_tmp))
        results.append(result)
        if progress:
            await progress(len(results), total, session_tmp.name)
        await asyncio.sleep(0)

    for retry_number, item in enumerate(retry_queue, start=1):
        if progress:
            await progress(total, total, f"повтор {retry_number}/{len(retry_queue)}: {item.session_tmp.name}")
        await asyncio.sleep(1)
        try:
            results[item.result_index] = await _copy_and_import_zip_account(
                config,
                session_tmp=item.session_tmp,
                json_tmp=item.json_tmp,
                created_by=created_by,
            )
        except Exception as exc:
            results[item.result_index] = _zip_error_result(item.session_tmp, exc, retried=True)
        await asyncio.sleep(0)

    imported_count = sum(1 for result in results if result.account_id)
    error_count = sum(1 for result in results if not result.account_id)
    summary = (
        f"SESSION: {len(session_files)}\n"
        f"JSON: {len(json_files)}\n"
        f"Импортировано: {imported_count}\n"
        f"Ошибок: {error_count}\n"
        f"Без JSON: {sum(1 for value in matches.values() if value is None)}"
    )
    if retry_queue:
        summary += f"\nПовторных попыток: {len(retry_queue)}"
    return results, summary
