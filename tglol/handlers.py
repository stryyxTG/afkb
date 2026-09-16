from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape
from math import ceil
from pathlib import Path
import logging
import re
import secrets
import shutil
import time
from typing import Any
import zipfile

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from telethon.errors import SessionPasswordNeededError

from tglol.config import Config
from tglol.db import (
    add_account,
    claim_accounts_for_worker,
    connect,
    count_accounts,
    count_accounts_by_stage,
    delete_account_row,
    delete_accounts_by_stage,
    get_account,
    list_accounts,
    list_accounts_by_scope,
    set_account_stage,
    update_account_status,
)
from tglol.desktop_profile import generated_account_json, random_desktop_runtime, utc_now_iso
from tglol.importer import download_document, import_zip
from tglol.json_utils import load_json, pick_api, pick_twofa, runtime_from_json, write_json
from tglol.keyboards import (
    ACCOUNTS_PER_PAGE,
    account_detail_menu,
    accounts_menu,
    proxy_menu,
    accounts_page_keyboard,
    add_account_menu,
    common_storage_sections_menu,
    confirm_check_account_menu,
    confirm_account_stage_menu,
    confirm_delete_account_menu,
    confirm_delete_common_stage_menu,
    registration_filter_menu,
    registration_service_menu,
    scan_sections_menu,
)
from tglol.paths import unique_path
from tglol.proxy_utils import check_proxy, mask_proxy, parse_proxy
from tglol.registration import (
    is_registration_service,
    parse_reg_origin,
    parse_service_filter,
    service_filter_label,
    service_label,
    services_from_storage,
    services_label,
)
from tglol.states import AddByCode, AddByZip, ProxyState
from tglol.telegram_service import (
    get_latest_telegram_code,
    get_recent_telegram_codes,
    inspect_session,
    inspect_session_with_freeze_check,
    send_code,
    sign_in_code,
    sign_in_password,
    user_fields,
)

router = Router()
logger = logging.getLogger(__name__)

CODE_WATCH_SECONDS = 10 * 60
CODE_WATCH_POLL_SECONDS = 10
CODE_WATCH_DATE_GRACE_SECONDS = 120
CODE_RETRY_WATCH_SECONDS = 2 * 60
CODE_WATCH_MAX_CONCURRENT_POLLS = 5
SCAN_CONCURRENCY = 5
SCAN_ACCOUNT_TIMEOUT = 35
BULK_CODE_LOGIN_LIMIT = 5
_active_retry_code_tasks: set[tuple[int, int, int]] = set()


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        config: Config = data["config"]
        user = data.get("event_from_user")

        if isinstance(event, Message):
            chat_id = event.chat.id
            text = (event.text or "").strip()
        elif isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message else None
            text = ""
        else:
            chat_id = None
            text = ""

        if not user:
            return None

        is_admin = user.id in config.admin_ids
        is_trigger_chat = bool(config.trigger_chat_id and chat_id is not None and chat_id == config.trigger_chat_id)
        if is_admin:
            return await handler(event, data)

        is_trigger_text = isinstance(event, Message) and bool(
            re.search(r"\bтг\b", text, flags=re.IGNORECASE)
        )
        is_worker_code_button = (
            isinstance(event, CallbackQuery)
            and bool(event.data)
            and (event.data.startswith("account:request_code:") or event.data.startswith("arc:"))
        )
        if is_trigger_chat and (is_trigger_text or is_worker_code_button):
            return await handler(event, data)

        return None


router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())


def _main_menu_text(config: Config) -> str:
    return "\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b"


def _proxy_menu_text(config: Config) -> str:
    if config.telegram_proxy:
        proxy_text = mask_proxy(config.telegram_proxy)
        return f"\u041f\u0440\u043e\u043a\u0441\u0438\n\u0422\u0435\u043a\u0443\u0449\u0438\u0439: <code>{escape(proxy_text)}</code>"
    return "\u041f\u0440\u043e\u043a\u0441\u0438\n\u0421\u0435\u0439\u0447\u0430\u0441 \u043f\u0440\u043e\u043a\u0441\u0438 \u043d\u0435\u0442."


def _write_env_value(name: str, value: str | None) -> None:
    path = Path(".env")
    line = f"{name}={value or ''}"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    for index, existing in enumerate(lines):
        if existing.startswith(f"{name}="):
            lines[index] = line
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _set_config_proxy(config: Config, value: str | None) -> None:
    cleaned = value.strip() if value else None
    _write_env_value("TELEGRAM_PROXY", cleaned)
    object.__setattr__(config, "telegram_proxy", cleaned)

def _pages(total: int) -> int:
    return max(1, ceil(total / ACCOUNTS_PER_PAGE))


def _text(value) -> str:
    return escape(str(value)) if value not in (None, "") else "-"


def _copyable(value) -> str:
    return f"<code>{escape(str(value))}</code>" if value not in (None, "") else "-"


def _username(value) -> str:
    if value in (None, ""):
        return "-"
    username = str(value)
    if not username.startswith("@"):
        username = f"@{username}"
    return f"<code>{escape(username)}</code>"


def _worker_name(worker) -> str:
    return " ".join(
        part for part in (worker.first_name, worker.last_name) if part
    ).strip() or "Без имени"


def _worker_text(worker) -> str:
    username = f"@{worker.username}" if worker.username else "-"
    return (
        f"<b>{escape(_worker_name(worker))}</b>\n"
        f"ID: <code>{worker.user_id}</code>\n"
        f"Username: <code>{escape(username)}</code>\n"
        f"Остаток лимита: <b>{worker.remaining_limit}</b>\n"
        f"Лимит после /reset: <b>{worker.configured_limit}</b>"
    )


async def _send_code_report(message: Message, lines: list[str]) -> None:
    """Send a long report as safe HTML preformatted chunks."""
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for raw_line in lines:
        raw_text = str(raw_line)
        raw_parts = [raw_text[index:index + 3000] for index in range(0, len(raw_text), 3000)] or [""]
        for raw_part in raw_parts:
            part = escape(raw_part)
            extra = len(part) + (1 if current else 0)
            if current and current_length + extra > 3500:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            current.append(part)
            current_length += len(part) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    for chunk in chunks or ["Отчёт пуст."]:
        await message.answer(f"<pre>{chunk}</pre>", parse_mode="HTML")


def _parse_worker_limit(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 1 <= value <= 100000 else None


def _parse_telegram_user_id(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 1 <= value <= 9_223_372_036_854_775_807 else None


async def _resolve_worker_identity(bot: Bot, config: Config, user_id: int) -> tuple[str, str | None, str | None]:
    if config.trigger_chat_id:
        try:
            member = await bot.get_chat_member(config.trigger_chat_id, user_id)
            member_status = getattr(member.status, "value", member.status)
            if member_status not in {"left", "kicked"}:
                user = member.user
                return user.first_name or "Без имени", user.last_name, user.username
        except Exception:
            pass
    try:
        chat = await bot.get_chat(user_id)
        return chat.first_name or chat.title or "Без имени", chat.last_name, chat.username
    except Exception as exc:
        logger.info("Cannot resolve worker %s before granting access: %s", user_id, exc)
        return "Имя пока неизвестно", None, None


def _is_issueable_account(account) -> bool:
    return account.status == "active" and account.account_stage not in {"issued", "processing"}


def _clean_issueable_accounts(config: Config) -> list:
    return [
        account
        for account in list_accounts_by_scope(config, excluded_account_stage=("issued", "processing"))
        if _is_issueable_account(account)
    ]


def _common_account_counts(config: Config) -> tuple[int, int]:
    clean_count = len(_clean_issueable_accounts(config))
    issued_count = count_accounts_by_stage(config, account_stage="issued")
    return clean_count, issued_count


def _origin_stage(origin: str) -> str | None:
    if origin in {"common", "common_clean"}:
        return "clean"
    if origin == "common_issued":
        return "issued"
    return None


def _origin_service_filters(origin: str) -> tuple[str | None, str | None]:
    return None, None


def _origin_is_valid(origin: str) -> bool:
    return origin in {"common", "common_clean", "common_issued"}


def _origin_for_account(account) -> str:
    if account.account_stage == "issued":
        return "common_issued"
    return "common"


def _stage_title(stage: str) -> str:
    if stage == "clean":
        return "Чистые"
    if stage == "issued":
        return "Выданные"
    if stage == "all":
        return "Все аккаунты"
    return "Неизвестный раздел"


def _stage_filter_title(
    stage: str,
    registration_service: str | None = None,
    excluded_service: str | None = None,
) -> str:
    if stage == "all":
        return "Все аккаунты"
    if stage == "clean":
        return _stage_title(stage)
    if stage == "issued":
        return _stage_title(stage)
    filter_label = service_filter_label(registration_service, excluded_service)
    if filter_label == "Все":
        return _stage_title(stage)
    return f"{_stage_title(stage)} {filter_label}"


def _code_login_runtime(config: Config) -> dict[str, Any]:
    runtime: dict[str, Any] = random_desktop_runtime()
    if config.telegram_proxy:
        runtime["proxy"] = config.telegram_proxy
        runtime["proxy_source"] = "global"
    return runtime

def _account_connection_params(account, config: Config) -> tuple[int, str, dict[str, str]]:
    raw_path = account.json_original_path or account.json_effective_path
    if not raw_path:
        raise RuntimeError("account JSON file is required")
    path = Path(raw_path)
    if not path.exists():
        raise RuntimeError(f"account JSON file not found: {path}")
    data = load_json(path)
    api_id, api_hash = pick_api(data, config)
    runtime = runtime_from_json(data)
    if not runtime.get("proxy") and config.telegram_proxy:
        runtime["proxy"] = config.telegram_proxy
        runtime["proxy_source"] = "global"
    return api_id, api_hash, runtime


async def check_account_validity(account, config: Config) -> dict[str, Any]:
    session_path = Path(account.session_path)
    if not session_path.exists():
        update_account_status(config, account.id, "missing_file")
        return {
            "account": account,
            "ok": False,
            "status": "skipped",
            "account_status": "missing_file",
            "reason": "Session файл не найден",
            "user": None,
        }

    try:
        api_id, api_hash, runtime = _account_connection_params(account, config)
    except Exception as exc:
        update_account_status(config, account.id, "bad_json")
        return {
            "account": account,
            "ok": False,
            "status": "skipped",
            "account_status": "bad_json",
            "reason": str(exc),
            "user": None,
        }

    try:
        result = await asyncio.wait_for(
            inspect_session_with_freeze_check(session_path, api_id, api_hash, runtime),
            timeout=SCAN_ACCOUNT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        result = {
            "ok": False,
            "status": "error",
            "account_status": "error",
            "reason": f"timeout {SCAN_ACCOUNT_TIMEOUT}s",
            "user": None,
        }
    except Exception as exc:
        result = {
            "ok": False,
            "status": "error",
            "account_status": "error",
            "reason": str(exc),
            "user": None,
        }

    account_status = str(result.get("account_status") or result.get("status") or "error")
    scan_status = str(result.get("status") or "error")
    if scan_status == "alive":
        status = "alive"
        ok = True
    elif scan_status in {"dead", "frozen", "need_2fa"}:
        status = scan_status
        ok = False
    elif scan_status == "skipped":
        status = "skipped"
        ok = False
    else:
        status = "error"
        ok = False

    update_account_status(config, account.id, account_status)
    return {
        "account": account,
        "ok": ok,
        "status": status,
        "account_status": account_status,
        "reason": str(result.get("reason") or account_status),
        "user": result.get("user"),
    }


def _scan_status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "alive": sum(1 for item in results if item["status"] == "alive"),
        "dead": sum(1 for item in results if item["status"] == "dead"),
        "frozen": sum(1 for item in results if item["status"] == "frozen"),
        "need_2fa": sum(1 for item in results if item["status"] == "need_2fa"),
        "error": sum(1 for item in results if item["status"] == "error"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
    }


def format_valid_check_progress(
    *,
    title: str,
    checked: int,
    total: int,
    alive: int,
    dead: int,
    frozen: int,
    need_2fa: int,
    error: int,
    skipped: int,
) -> str:
    return (
        f"<b>{escape(title)}</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"Проверено: <b>{checked}</b>\n"
        f"Живых: <b>{alive}</b>\n"
        f"Мертвых: <b>{dead}</b>\n"
        f"Замороженных: <b>{frozen}</b>\n"
        f"2FA: <b>{need_2fa}</b>\n"
        f"Ошибок: <b>{error}</b>\n"
        f"Пропущено: <b>{skipped}</b>"
    )


async def run_accounts_valid_check(config: Config, accounts: list, *, progress_cb=None) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
    total_accounts = len(accounts)
    total_attempts = total_accounts
    checked_attempts = 0
    results_by_id: dict[int, dict[str, Any]] = {}
    order: list[int] = []

    def current_results() -> list[dict[str, Any]]:
        return [results_by_id[account_id] for account_id in order]

    async def report_progress() -> None:
        if not progress_cb:
            return
        counts = _scan_status_counts(current_results())
        await progress_cb(
            checked=checked_attempts,
            total=total_attempts,
            alive=counts["alive"],
            dead=counts["dead"],
            frozen=counts["frozen"],
            need_2fa=counts["need_2fa"],
            error=counts["error"],
            skipped=counts["skipped"],
        )

    async def worker(account, *, attempt: int) -> None:
        nonlocal checked_attempts
        async with semaphore:
            result = await check_account_validity(account, config)
            account_id = int(getattr(account, "id", len(order)))
            if account_id not in results_by_id:
                order.append(account_id)
            results_by_id[account_id] = result
            checked_attempts += 1
            logger.info(
                "Scan account result attempt=%s id=%s phone=%s status=%s account_status=%s reason=%s",
                attempt,
                getattr(account, "id", None),
                getattr(account, "phone", None),
                result["status"],
                result["account_status"],
                result["reason"],
            )
            await report_progress()

    await asyncio.gather(*(worker(account, attempt=1) for account in accounts))

    retry_accounts = [
        item["account"]
        for item in current_results()
        if item["status"] != "alive"
    ]
    if retry_accounts:
        total_attempts += len(retry_accounts)
        logger.info("Retrying failed scan accounts count=%s", len(retry_accounts))
        await asyncio.gather(*(worker(account, attempt=2) for account in retry_accounts))

    results = current_results()
    counts = _scan_status_counts(results)
    return {
        "total": total_accounts,
        "checked": len(results),
        "attempts": checked_attempts,
        "alive": [item["account"] for item in results if item["status"] == "alive"],
        "dead": [item["account"] for item in results if item["status"] == "dead"],
        "frozen": [item["account"] for item in results if item["status"] == "frozen"],
        "need_2fa": [item["account"] for item in results if item["status"] == "need_2fa"],
        "error": [item["account"] for item in results if item["status"] == "error"],
        "skipped": [item["account"] for item in results if item["status"] == "skipped"],
        "results": results,
        **counts,
    }
def _account_file_paths(account) -> list[Path]:
    paths: list[Path] = []
    for raw in (account.session_path, account.json_original_path, account.json_effective_path):
        if raw:
            path = Path(raw)
            if path not in paths:
                paths.append(path)
    return paths


def _resolved_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_allowed_storage_file(config: Config, path: Path) -> bool:
    resolved = _resolved_path(path)
    allowed_roots = (config.data_dir, config.sessions_dir, config.json_dir, config.temp_dir)
    for root in allowed_roots:
        try:
            resolved.relative_to(_resolved_path(root))
            return path.exists() and path.is_file()
        except ValueError:
            continue
    return False


def _delete_local_account_files(config: Config, accounts: list) -> int:
    selected_ids = {account.id for account in accounts}
    remaining_paths = {
        str(_resolved_path(path))
        for account in list_accounts_by_scope(config)
        if account.id not in selected_ids
        for path in _account_file_paths(account)
    }
    removed = 0
    seen: set[str] = set()
    for account in accounts:
        for path in _account_file_paths(account):
            resolved = str(_resolved_path(path))
            if resolved in seen or resolved in remaining_paths:
                continue
            seen.add(resolved)
            if not _is_allowed_storage_file(config, path):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _make_accounts_zip(config: Config, accounts: list, stage: str) -> tuple[Path, int]:
    zip_path = unique_path(config.temp_dir, f"accounts_{stage}_{secrets.token_hex(4)}.zip")
    files_count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive_names: set[str] = set()
        for account in accounts:
            added_paths: set[str] = set()
            for path in _account_file_paths(account):
                resolved = str(_resolved_path(path))
                if resolved in added_paths or not path.exists() or not path.is_file():
                    continue
                added_paths.add(resolved)
                archive_name = path.name
                if archive_name in archive_names:
                    archive_name = f"{account.id}_{path.name}"
                archive_names.add(archive_name)
                archive.write(path, arcname=archive_name)
                files_count += 1
    return zip_path, files_count


def _normalize_login_code(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def _normalize_login_phone(raw: str) -> str | None:
    raw = (raw or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if raw.startswith("00") and len(digits) > 2:
        digits = digits[2:]
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}"



def _phone_without_plus(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "") or "-"



def _account_twofa_password(account) -> str | None:
    if account.twofa_password:
        return str(account.twofa_password)
    raw_path = account.json_original_path or account.json_effective_path
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.exists():
        return None
    try:
        return pick_twofa(load_json(path))
    except Exception as exc:
        logger.info("Cannot read 2FA from account JSON %s: %s", account.id, exc)
        return None

def _code_message_is_new(message, started_at: datetime) -> bool:
    message_date = message.date
    if message_date is None:
        return True
    if message_date.tzinfo is None:
        message_date = message_date.replace(tzinfo=timezone.utc)
    return message_date >= started_at - timedelta(seconds=CODE_WATCH_DATE_GRACE_SECONDS)



def _log_code_watcher_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Code watcher failed", exc_info=(type(exc), exc, exc.__traceback__))

async def _send_code_delivery(
    bot: Bot,
    *,
    chat_id: int,
    account,
    code: str,
    requester_user_id: int,
    reply_to_message_id: int | None = None,
) -> None:
    phone = _phone_without_plus(account.phone)
    lines = [escape(phone), f"<code>{escape(code)}</code>"]
    twofa = _account_twofa_password(account)
    if twofa:
        lines.append(f"2FA: <code>{escape(twofa)}</code>")
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u0441\u043d\u043e\u0432\u0430",
                    callback_data=f"arc:{account.id}:{chat_id}:{requester_user_id}:{reply_to_message_id or 0}",
                )
            ]
        ]
    )
    last_error: Exception | None = None
    reply_variants = [reply_to_message_id]
    if reply_to_message_id is not None:
        reply_variants.append(None)
    for reply_id in reply_variants:
        for attempt in range(3):
            try:
                await bot.send_message(
                    chat_id,
                    "\n".join(lines),
                    reply_to_message_id=reply_id,
                    parse_mode="HTML",
                    reply_markup=markup,
                )
                return
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
        if reply_id is not None:
            logger.info("Code delivery with reply failed for account %s, retrying without reply: %s", account.id, last_error)
    if last_error:
        raise last_error


def _terminal_code_poll_status(exc: Exception) -> str | None:
    message = str(exc).casefold()
    if "session is not authorized" in message:
        return "unauthorized"
    if "account json file not found" in message or "account json file is required" in message:
        return "bad_json"
    return None


async def _poll_new_telegram_code_message(
    config: Config,
    account,
    *,
    started_at: datetime,
    seen_keys: set[str] | None = None,
):
    seen = seen_keys if seen_keys is not None else set()
    current = get_account(config, account.id)
    if not current:
        return None, None, True
    try:
        session_path = Path(current.session_path)
        if not session_path.exists():
            update_account_status(config, current.id, "missing_file")
            return current, None, True
        api_id, api_hash, runtime = _account_connection_params(current, config)
        messages = await get_recent_telegram_codes(session_path, api_id, api_hash, runtime, limit=50)
    except Exception as exc:
        terminal_status = _terminal_code_poll_status(exc)
        if terminal_status:
            update_account_status(config, current.id, terminal_status)
            logger.info("Stop polling Telegram code for account %s: %s", account.id, exc)
            return current, None, True
        logger.info("Cannot poll Telegram code for account %s: %s", account.id, exc)
        return current, None, False

    messages = sorted(
        messages,
        key=lambda item: item.date or datetime.min.replace(tzinfo=timezone.utc),
    )
    for code_message in messages:
        if code_message.key in seen:
            continue
        if not _code_message_is_new(code_message, started_at):
            seen.add(code_message.key)
            continue
        return current, code_message, False
    return current, None, False


async def _find_new_telegram_code_message(
    config: Config,
    account,
    *,
    started_at: datetime,
    deadline: float,
    seen_keys: set[str] | None = None,
):
    seen = seen_keys if seen_keys is not None else set()
    while time.monotonic() < deadline:
        current, code_message, terminal = await _poll_new_telegram_code_message(
            config,
            account,
            started_at=started_at,
            seen_keys=seen,
        )
        if not current or code_message or terminal:
            return current, code_message, terminal
        if time.monotonic() < deadline:
            await asyncio.sleep(CODE_WATCH_POLL_SECONDS)
    return get_account(config, account.id), None, False


def _restore_clean_stage_after_code_timeout(config: Config, account) -> str:
    if account.account_stage == "reg":
        services = services_from_storage(account.registration_services, account.registration_service)
        if services:
            set_account_stage(config, account.id, "reg", registration_services=services)
            return "reg"
    set_account_stage(config, account.id, "nereg")
    return "nereg"


def _finalize_requested_account_stages(config: Config, accounts: list, code_received_ids: set[int]) -> dict[str, int]:
    target_all_as_issued = bool(code_received_ids)
    result = {"issued": 0, "restored": 0, "skipped": 0}
    for account in accounts:
        current = get_account(config, account.id)
        if not current or current.account_stage != "processing":
            result["skipped"] += 1
            continue
        if target_all_as_issued:
            set_account_stage(config, account.id, "issued")
            result["issued"] += 1
        else:
            _restore_clean_stage_after_code_timeout(config, account)
            result["restored"] += 1
    return result
async def _watch_requested_account_codes(
    bot: Bot,
    config: Config,
    accounts: list,
    *,
    chat_id: int,
    requester_user_id: int,
    reply_to_message_id: int | None,
    started_at: datetime,
) -> None:
    seen_keys: dict[int, set[str]] = {account.id: set() for account in accounts}
    pending_ids = {account.id for account in accounts}
    code_received_ids: set[int] = set()
    poll_semaphore = asyncio.Semaphore(CODE_WATCH_MAX_CONCURRENT_POLLS)
    deadline = time.monotonic() + CODE_WATCH_SECONDS
    stages_finalized_after_delivery = False

    async def poll_account(account):
        async with poll_semaphore:
            current, code_message, terminal = await _poll_new_telegram_code_message(
                config,
                account,
                started_at=started_at,
                seen_keys=seen_keys.setdefault(account.id, set()),
            )
            return account, current, code_message, terminal

    try:
        while pending_ids and time.monotonic() < deadline:
            pending_accounts = [account for account in accounts if account.id in pending_ids]
            results = await asyncio.gather(
                *(poll_account(account) for account in pending_accounts),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Code watcher poll task failed: %s", result)
                    continue
                account, current, code_message, terminal = result
                if account.id not in pending_ids:
                    continue
                if terminal:
                    pending_ids.discard(account.id)
                    continue
                if not current or not code_message:
                    continue
                try:
                    await _send_code_delivery(
                        bot,
                        chat_id=chat_id,
                        account=current,
                        code=code_message.code,
                        requester_user_id=requester_user_id,
                        reply_to_message_id=reply_to_message_id,
                    )
                    seen_keys.setdefault(account.id, set()).add(code_message.key)
                    pending_ids.discard(account.id)
                    code_received_ids.add(account.id)
                    if not stages_finalized_after_delivery:
                        stage_result = _finalize_requested_account_stages(config, accounts, code_received_ids)
                        stages_finalized_after_delivery = True
                        logger.info(
                            "Code watcher immediately finalized requested accounts after delivery: issued=%s restored=%s skipped=%s",
                            stage_result["issued"],
                            stage_result["restored"],
                            stage_result["skipped"],
                        )
                except Exception as exc:
                    logger.warning("Cannot send Telegram code for account %s: %s", account.id, exc)
            if pending_ids and time.monotonic() < deadline:
                await asyncio.sleep(CODE_WATCH_POLL_SECONDS)
    finally:
        stage_result = _finalize_requested_account_stages(config, accounts, code_received_ids)
        logger.info(
            "Code watcher finalized requested accounts: code_received=%s issued=%s restored=%s skipped=%s",
            bool(code_received_ids),
            stage_result["issued"],
            stage_result["restored"],
            stage_result["skipped"],
        )
def _parse_trigger_request_count(text: str) -> int:
    text = (text or "").strip().lower()
    patterns = (
        r"\bтг\b\s*(\d+)",
        r"(\d+)\s*\bтг\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return max(1, min(int(match.group(1)), 20))
    return 1


def _is_trigger_message(message: Message, config: Config) -> bool:
    if not config.trigger_chat_id:
        return False
    if message.chat.id != config.trigger_chat_id:
        return False
    text = (message.text or "").strip()
    if not text:
        return False
    return bool(re.search(r"\bтг\b", text, flags=re.IGNORECASE))


async def _notify_owners(bot: Bot, config: Config, *, reason: str, account_phone: str | None, requester_chat_id: int | None, requester_user_id: int | None) -> None:
    text = (
        f"<b>Аккаунт удалён по триггеру</b>\n"
        f"Причина: {escape(reason)}\n"
        f"Номер: {escape(account_phone or '-')}\n"
        f"Чат: {requester_chat_id or '-'}\n"
        f"Пользователь: {requester_user_id or '-'}"
    )
    for owner_id in sorted(config.admin_ids):
        try:
            await bot.send_message(owner_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Cannot notify owner %s: %s", owner_id, exc)


async def _give_out_accounts(message: Message, bot: Bot, config: Config) -> None:
    requested = _parse_trigger_request_count(message.text or "")
    requested_count = requested

    requester_id = message.from_user.id if message.from_user else 0
    claimed_accounts: list = []
    skipped_ids: set[int] = set()
    while len(claimed_accounts) < requested:
        need = requested - len(claimed_accounts)
        accounts: list = []
        available_accounts = [
            account
            for account in _clean_issueable_accounts(config)
            if account.id not in skipped_ids
        ]
        if not available_accounts:
            break
        for account in available_accounts:
            if not account.session_path:
                skipped_ids.add(account.id)
                continue
            session_path = Path(account.session_path)
            if not session_path.exists():
                skipped_ids.add(account.id)
                removed_files = _delete_local_account_files(config, [account])
                delete_account_row(config, account.id)
                await _notify_owners(
                    bot,
                    config,
                    reason="session file missing",
                    account_phone=account.phone,
                    requester_chat_id=message.chat.id,
                    requester_user_id=message.from_user.id if message.from_user else None,
                )
                logger.info("Removed invalid account %s because session file missing", account.id)
                continue
            try:
                _account_connection_params(account, config)
            except Exception as exc:
                skipped_ids.add(account.id)
                logger.info("Skipping account %s before giveout because JSON is not usable: %s", account.id, exc)
                continue
            accounts.append(account)
            if len(accounts) >= need:
                break
        if not accounts:
            break

        claimed_batch = claim_accounts_for_worker(
            config,
            [account.id for account in accounts],
            worker_id=None,
            requested_count=requested_count if not claimed_accounts else 0,
            reserve_stage="processing",
        )
        if not claimed_batch:
            skipped_ids.update(account.id for account in accounts)
            continue
        claimed_accounts.extend(claimed_batch)
    if not claimed_accounts:
        await message.answer("Сейчас свободных аккаунтов нет.")
        return
    started_at = datetime.now(timezone.utc)
    phone_lines = [f"<code>{escape(_phone_without_plus(account.phone))}</code>" for account in claimed_accounts]
    await message.answer("\u041d\u043e\u043c\u0435\u0440\u0430:\n" + "\n".join(phone_lines), reply_to_message_id=message.message_id)

    watcher = asyncio.create_task(
        _watch_requested_account_codes(
            bot,
            config,
            claimed_accounts,
            chat_id=message.chat.id,
            requester_user_id=requester_id,
            reply_to_message_id=message.message_id,
            started_at=started_at,
        )
    )
    watcher.add_done_callback(_log_code_watcher_result)



def _promote_login_session(config: Config, temp_session_path: Path, phone: str, login_id: str) -> Path:
    phone_digits = phone.lstrip("+")
    final_path = unique_path(config.sessions_dir, f"{phone_digits}_{login_id}.session")
    if temp_session_path.resolve() == final_path.resolve():
        return final_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if not temp_session_path.exists():
        raise RuntimeError(f"Temporary session file not found: {temp_session_path}")
    shutil.move(str(temp_session_path), str(final_path))
    return final_path


async def _show_account_page(callback: CallbackQuery, config: Config, origin: str, ref_id: int, page: int) -> None:
    if not _origin_is_valid(origin):
        await callback.answer("Это хранилище больше недоступно.", show_alert=True)
        return

    stage = _origin_stage(origin)

    if stage is None:
        total = count_accounts(config)
    elif stage == "clean":
        total = len(_clean_issueable_accounts(config))
    else:
        total = count_accounts_by_stage(config, account_stage=stage)

    page = max(0, min(page, _pages(total) - 1))
    if stage == "clean":
        accounts = _clean_issueable_accounts(config)[page * ACCOUNTS_PER_PAGE:(page + 1) * ACCOUNTS_PER_PAGE]
    elif stage is None:
        accounts = list_accounts(
            config,
            limit=ACCOUNTS_PER_PAGE,
            offset=page * ACCOUNTS_PER_PAGE,
        )
    else:
        accounts = list_accounts(
            config,
            limit=ACCOUNTS_PER_PAGE,
            offset=page * ACCOUNTS_PER_PAGE,
            account_stage=stage,
        )

    title = "Хранилище"
    if total == 0:
        text = f"{title}\n\nАккаунтов пока нет."
    else:
        text = f"{title}\n\nВсего: {total}\nСтраница: {page + 1}/{_pages(total)}"

    await callback.message.edit_text(
        text,
        reply_markup=accounts_page_keyboard(
            accounts,
            total=total,
            page=page,
            origin=origin,
            ref_id=ref_id,
        ),
    )
    await callback.answer()


def _account_detail_text(account) -> str:
    full_name = " ".join(
        part for part in (account.first_name, account.last_name)
        if part not in (None, "")
    ).strip()
    full_name = full_name or "-"

    return (
        f"<b>Аккаунт #{account.id}</b>\n"
        f"Статус: <code>{_text(account.status)}</code>\n\n"
        f"Имя: <b>{escape(full_name)}</b>\n"
        f"Номер: {_copyable(account.phone)}\n"
        f"Username: {_username(account.username)}\n"
        f"User ID: {_copyable(account.telegram_user_id)}\n\n"
        f"JSON: {_text(account.json_source)}\n"
        f"Источник: {_text(account.source_type)}"
    )


def _service_selection_text(selected_services: list[str]) -> str:
    selected = ", ".join(service_label(service) for service in selected_services) if selected_services else "не выбрано"
    return f"Выбери один или несколько сервисов, где аккаунт зареган:\n\nВыбрано: {selected}"


async def finalize_code_login(
    message: Message,
    state: FSMContext,
    config: Config,
    *,
    twofa: str | None,
    user,
) -> None:
    data = await state.get_data()
    await _finalize_code_login_impl(
        message,
        config,
        session_path=data["session_path"],
        phone=data["phone"],
        login_id=data["login_id"],
        runtime=data["runtime"],
        admin_id=data.get("admin_id") or (message.from_user.id if message.from_user else None),
        twofa=twofa,
        user=user,
        clear_state=True,
        state=state,
    )


async def _finalize_code_login_impl(
    message: Message,
    config: Config,
    *,
    session_path: str | Path,
    phone: str,
    login_id: str,
    runtime: dict[str, str],
    admin_id: int | None,
    twofa: str | None,
    user,
    clear_state: bool,
    state: FSMContext | None = None,
    announce: bool = True,
) -> int:
    session_path = _promote_login_session(
        config,
        Path(session_path),
        phone,
        login_id,
    )
    fields = user_fields(user)
    json_path = unique_path(config.json_dir, session_path.with_suffix(".json").name)
    generated = generated_account_json(
        config,
        runtime=runtime,
        twofa=twofa,
        session_file=session_path.name,
        phone=fields["phone"],
        user_id=fields["telegram_user_id"],
        username=fields["username"],
        first_name=fields["first_name"],
        last_name=fields["last_name"],
    )
    write_json(json_path, generated)

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
            "json_original_path": None,
            "json_effective_path": str(json_path),
            "json_source": "generated",
            "twofa_password": twofa,
            "source_type": "code",
            "status": "active",
            "created_by": admin_id,
            "created_at": now,
            "updated_at": now,
        },
    )
    if clear_state and state is not None:
        await state.clear()
    if announce:
        await message.answer(
            (
                f"Аккаунт добавлен в общее хранилище.\n"
                f"ID: {account_id}\n"
                f"Телефон: {fields['phone'] or '-'}"
            ),
            reply_markup=accounts_menu(),
        )

    return account_id

@router.message(F.text == "/cancel")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=accounts_menu())


@router.message(Command("reset"))
async def reset_workers_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Логика воркеров отключена.")


@router.message(Command("stats"))
async def worker_stats_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Логика воркеров отключена.")


@router.message(Command("workers"))
async def workers_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Логика воркеров отключена.")

@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    await message.answer(_main_menu_text(config), reply_markup=accounts_menu())


@router.callback_query(F.data == "accounts:menu")
async def show_accounts_menu(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await callback.message.edit_text(_main_menu_text(config), reply_markup=accounts_menu())
    await callback.answer()


@router.callback_query(F.data == "accounts:common_sections")
async def show_common_account_sections(callback: CallbackQuery, config: Config) -> None:
    clean_count, issued_count = _common_account_counts(config)
    await callback.message.edit_text(
        "Хранилище",
        reply_markup=common_storage_sections_menu(clean_count=clean_count, issued_count=issued_count),
    )
    await callback.answer()



@router.callback_query(F.data == "scan_accounts")
async def scan_accounts_menu(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    clean_count, issued_count = _common_account_counts(config)
    await callback.message.edit_text(
        "<b>Скан</b>\n\nВыбери отдел для валид-чека.",
        reply_markup=scan_sections_menu(clean_count=clean_count, issued_count=issued_count),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("scan_accounts:"))
async def scan_accounts_handler(callback: CallbackQuery, config: Config) -> None:
    section = callback.data.split(":", 1)[1]
    if section == "clean":
        title = "Валид-чек: чистые"
        accounts = _clean_issueable_accounts(config)
    elif section == "issued":
        title = "Валид-чек: выданные"
        accounts = list_accounts_by_scope(config, account_stage="issued")
    else:
        await callback.answer("Неизвестный отдел.", show_alert=True)
        return

    if not accounts:
        clean_count, issued_count = _common_account_counts(config)
        await callback.message.edit_text(
            "Аккаунтов для проверки нет.",
            reply_markup=scan_sections_menu(clean_count=clean_count, issued_count=issued_count),
        )
        await callback.answer()
        return

    await callback.answer("Проверка началась.")
    last_update = 0.0
    await callback.message.edit_text(
        format_valid_check_progress(
            title=title,
            checked=0,
            total=len(accounts),
            alive=0,
            dead=0,
            frozen=0,
            need_2fa=0,
            error=0,
            skipped=0,
        )
    )

    async def progress_cb(*, checked: int, total: int, alive: int, dead: int, frozen: int, need_2fa: int, error: int, skipped: int) -> None:
        nonlocal last_update
        now = time.monotonic()
        if checked != total and now - last_update < 2:
            return
        last_update = now
        try:
            await callback.message.edit_text(
                format_valid_check_progress(
                    title=f"{title}: проверка идет",
                    checked=checked,
                    total=total,
                    alive=alive,
                    dead=dead,
                    frozen=frozen,
                    need_2fa=need_2fa,
                    error=error,
                    skipped=skipped,
                )
            )
        except Exception:
            pass

    result = await run_accounts_valid_check(config, accounts, progress_cb=progress_cb)
    clean_count, issued_count = _common_account_counts(config)
    problem_lines = []
    for item in result["results"]:
        if item["status"] == "alive":
            continue
        account = item["account"]
        label = account.phone or account.username or account.telegram_user_id or account.id
        problem_lines.append(
            f"#{account.id} | {escape(str(label))} | {escape(item['account_status'])} | {escape(str(item['reason']))}"
        )
    if len(problem_lines) > 10:
        problem_lines = [*problem_lines[:10], f"...и еще {len(problem_lines) - 10}"]

    details = ""
    if problem_lines:
        details = "\n\n<b>Проблемные:</b>\n<pre>" + "\n".join(problem_lines) + "</pre>"

    await callback.message.edit_text(
        format_valid_check_progress(
            title=f"{title}: завершено",
            checked=result["checked"],
            total=result["total"],
            alive=result["alive"],
            dead=result["dead"],
            frozen=result["frozen"],
            need_2fa=result["need_2fa"],
            error=result["error"],
            skipped=result["skipped"],
        ) + details,
        reply_markup=scan_sections_menu(clean_count=clean_count, issued_count=issued_count),
    )

@router.callback_query(F.data == "proxy:menu")
async def show_proxy_menu(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    await callback.message.edit_text(_proxy_menu_text(config), reply_markup=proxy_menu(has_proxy=bool(config.telegram_proxy)))
    await callback.answer()


@router.callback_query(F.data == "proxy:set")
async def ask_proxy(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.set_state(ProxyState.waiting_proxy)
    await callback.message.edit_text(
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043f\u0440\u043e\u043a\u0441\u0438 \u043b\u044e\u0431\u044b\u043c \u0444\u043e\u0440\u043c\u0430\u0442\u043e\u043c:\n"
        "<code>host:port</code>\n"
        "<code>host:port:login:password</code>\n"
        "<code>socks5://login:password@host:port</code>\n"
        "<code>tg://socks?server=HOST&amp;port=PORT&amp;user=USER&amp;pass=PASS</code>\n"
        "<code>tg://proxy?server=HOST&amp;port=PORT&amp;secret=SECRET</code>",
        reply_markup=proxy_menu(has_proxy=bool(config.telegram_proxy)),
    )
    await callback.answer()


@router.message(ProxyState.waiting_proxy)
async def save_proxy(message: Message, state: FSMContext, config: Config) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("\u041f\u0440\u043e\u043a\u0441\u0438 \u043f\u0443\u0441\u0442\u043e\u0439. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u0442\u0440\u043e\u043a\u0443 \u043f\u0440\u043e\u043a\u0441\u0438 \u0438\u043b\u0438 /cancel.")
        return
    try:
        parsed = parse_proxy(raw)
        if parsed is None:
            raise ValueError("proxy is empty")
    except Exception as exc:
        await message.answer(f"\u0424\u043e\u0440\u043c\u0430\u0442 \u043f\u0440\u043e\u043a\u0441\u0438 \u043d\u0435 \u043f\u0440\u0438\u043d\u044f\u0442: {escape(str(exc))}")
        return
    _set_config_proxy(config, raw)
    await state.clear()
    await message.answer(
        f"\u041f\u0440\u043e\u043a\u0441\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d:\n<code>{escape(mask_proxy(parsed))}</code>",
        reply_markup=proxy_menu(has_proxy=True),
    )


@router.callback_query(F.data == "proxy:check")
async def check_current_proxy(callback: CallbackQuery, config: Config) -> None:
    if not config.telegram_proxy:
        await callback.answer("\u041f\u0440\u043e\u043a\u0441\u0438 \u0435\u0449\u0451 \u043d\u0435 \u0437\u0430\u0434\u0430\u043d.", show_alert=True)
        return
    started = time.perf_counter()
    try:
        ok = await check_proxy(config.telegram_proxy, timeout=10.0)
    except Exception as exc:
        await callback.message.answer(f"\u0427\u0435\u043a \u043f\u0440\u043e\u043a\u0441\u0438 \u0443\u043f\u0430\u043b: {escape(str(exc))}")
        await callback.answer()
        return
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    status = "OK" if ok else "FAIL"
    await callback.message.answer(
        f"\u041f\u0440\u043e\u043a\u0441\u0438: <code>{escape(mask_proxy(config.telegram_proxy))}</code>\n"
        f"\u0421\u0442\u0430\u0442\u0443\u0441: <b>{status}</b>\n"
        f"\u041f\u0438\u043d\u0433: <code>{elapsed_ms} ms</code>"
    )
    await callback.answer()


@router.callback_query(F.data == "proxy:delete")
async def delete_proxy(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    _set_config_proxy(config, None)
    await callback.message.edit_text(_proxy_menu_text(config), reply_markup=proxy_menu(has_proxy=False))
    await callback.answer("\u041f\u0440\u043e\u043a\u0441\u0438 \u0443\u0434\u0430\u043b\u0451\u043d.")

@router.callback_query(F.data == "accounts:add")
async def show_add_account_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Все новые аккаунты попадут в общее хранилище.\n\nВыбери способ добавления аккаунта.",
        reply_markup=add_account_menu(),
    )
    await callback.answer()


def _input_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _parse_bulk_login_phones(text: str) -> tuple[list[str], str | None]:
    lines = _input_lines(text)
    if not lines:
        return [], "Отправь номер телефона."
    if len(lines) > BULK_CODE_LOGIN_LIMIT:
        return [], f"За раз можно максимум {BULK_CODE_LOGIN_LIMIT} номеров."
    phones: list[str] = []
    for index, line in enumerate(lines, start=1):
        phone = _normalize_login_phone(line)
        if not phone:
            return [], f"Номер в строке {index} некорректный: {escape(line)}"
        if phone in phones:
            return [], f"Номер повторяется: {escape(phone)}"
        phones.append(phone)
    return phones, None


def _parse_bulk_login_codes(text: str, expected_count: int) -> tuple[list[str], str | None]:
    lines = _input_lines(text)
    if len(lines) != expected_count:
        return [], f"Нужно отправить {expected_count} кодов: каждый код с новой строки, в том же порядке."
    codes: list[str] = []
    for index, line in enumerate(lines, start=1):
        code = _normalize_login_code(line)
        if not 5 <= len(code) <= 8:
            return [], f"Код в строке {index} должен быть длиной 5-8 цифр."
        codes.append(code)
    return codes, None


async def _request_code_login_entry(config: Config, *, phone: str, admin_id: int) -> dict[str, Any]:
    runtime = _code_login_runtime(config)
    login_id = secrets.token_hex(4)
    phone_digits = phone.lstrip("+")
    session_path = unique_path(config.temp_dir, f"temp_session_{admin_id}_{phone_digits}_{login_id}.session")
    try:
        code_request = await send_code(
            session_path,
            phone,
            config.telegram_api_id,
            config.telegram_api_hash,
            runtime,
        )
    except Exception as exc:
        try:
            session_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "phone": phone, "error": str(exc)}
    return {
        "ok": True,
        "phone": phone,
        "phone_code_hash": code_request.phone_code_hash,
        "session_path": str(session_path),
        "login_id": login_id,
        "admin_id": admin_id,
        "runtime": runtime,
        "already_authorized": code_request.already_authorized,
        "user": code_request.user,
    }


async def _complete_bulk_code_login_entry(message: Message, config: Config, entry: dict[str, Any], code: str) -> dict[str, Any]:
    try:
        user = await sign_in_code(
            Path(entry["session_path"]),
            entry["phone"],
            code,
            entry["phone_code_hash"],
            config.telegram_api_id,
            config.telegram_api_hash,
            entry["runtime"],
        )
    except SessionPasswordNeededError:
        return {"ok": False, "phone": entry["phone"], "status": "twofa", "error": "нужен 2FA"}
    except Exception as exc:
        logger.exception("Bulk code login sign_in failed for %s", entry.get("phone"))
        return {"ok": False, "phone": entry["phone"], "status": "error", "error": str(exc)}

    account_id = await _finalize_code_login_impl(
        message,
        config,
        session_path=entry["session_path"],
        phone=entry["phone"],
        login_id=entry["login_id"],
        runtime=entry["runtime"],
        admin_id=entry.get("admin_id"),
        twofa=None,
        user=user,
        clear_state=False,
        announce=False,
    )
    return {"ok": True, "phone": entry["phone"], "account_id": account_id}


async def complete_bulk_codes(message: Message, state: FSMContext, config: Config, codes: list[str]) -> None:
    data = await state.get_data()
    entries = list(data.get("bulk_logins") or [])
    if not entries:
        await message.answer("Не удалось сохранить пачку. Начни заново.", reply_markup=add_account_menu())
        await state.clear()
        return
    if len(codes) != len(entries):
        await message.answer(f"Нужно {len(entries)} кодов, а пришло {len(codes)}.")
        return

    progress = await message.answer(f"Проверяю коды: 0/{len(entries)}...")
    results = await asyncio.gather(
        *(_complete_bulk_code_login_entry(message, config, entry, code) for entry, code in zip(entries, codes)),
        return_exceptions=True,
    )

    added: list[str] = []
    errors: list[str] = []
    for entry, result in zip(entries, results):
        phone = entry.get("phone") or "-"
        if isinstance(result, Exception):
            errors.append(f"{phone}: {type(result).__name__}")
            continue
        if result.get("ok"):
            added.append(f"#{result['account_id']} · {phone}")
        else:
            errors.append(f"{phone}: {result.get('error') or result.get('status') or 'error'}")

    await state.clear()
    try:
        await progress.edit_text("Массовая проверка кодов завершена.")
    except Exception:
        pass

    lines = [
        "Массовое добавление по коду завершено.",
        f"Добавлено: {len(added)}",
        f"Ошибок: {len(errors)}",
    ]
    if added:
        lines.extend(["", "Добавлены:", *added[:20]])
    if errors:
        lines.extend(["", "Ошибки:", *errors[:20]])
    await message.answer("\n".join(lines), reply_markup=accounts_menu())

@router.callback_query(F.data == "accounts:add:code")
async def add_by_code_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddByCode.waiting_phone)
    await callback.message.edit_text(
        "Отправь номер телефона или до 5 номеров с новой строки.\n\n"
        "Например:\n+15074486037\n+15074486038\n+15074486039"
    )
    await callback.answer()


@router.message(AddByCode.waiting_phone)
async def add_by_code_phone(message: Message, state: FSMContext, config: Config) -> None:
    phones, parse_error = _parse_bulk_login_phones(message.text or "")
    if parse_error:
        await message.answer(parse_error)
        return
    if len(phones) > 1:
        admin_id = message.from_user.id if message.from_user else 0
        progress = await message.answer(f"Отправляю секретные числа: 0/{len(phones)}...")
        results = await asyncio.gather(
            *(_request_code_login_entry(config, phone=phone, admin_id=admin_id) for phone in phones),
            return_exceptions=True,
        )
        waiting_entries: list[dict[str, Any]] = []
        added: list[str] = []
        errors: list[str] = []
        for phone, result in zip(phones, results):
            if isinstance(result, Exception):
                errors.append(f"{phone}: {type(result).__name__}")
                continue
            if not result.get("ok"):
                errors.append(f"{phone}: {result.get('error') or 'error'}")
                continue
            if result.get("already_authorized") and result.get("user"):
                account_id = await _finalize_code_login_impl(
                    message,
                    config,
                    session_path=result["session_path"],
                    phone=result["phone"],
                    login_id=result["login_id"],
                    runtime=result["runtime"],
                    admin_id=result.get("admin_id"),
                    twofa=None,
                    user=result["user"],
                    clear_state=False,
                    announce=False,
                )
                added.append(f"#{account_id} · {phone}")
                continue
            if result.get("already_authorized"):
                errors.append(f"{phone}: уже авторизован, но Telegram не вернул данные")
                continue
            waiting_entries.append(result)

        try:
            await progress.edit_text(f"Секретные числа отправлены: {len(waiting_entries)}/{len(phones)}")
        except Exception:
            pass
        if not waiting_entries:
            await state.clear()
            lines = ["Пачка обработана.", f"Добавлено сразу: {len(added)}", f"Ошибок: {len(errors)}"]
            if added:
                lines.extend(["", "Добавлены:", *added])
            if errors:
                lines.extend(["", "Ошибки:", *errors])
            await message.answer("\n".join(lines), reply_markup=accounts_menu())
            return

        await state.update_data(
            bulk_logins=waiting_entries,
            bulk_added=added,
            bulk_errors=errors,
            login_started_at=utc_now_iso(),
        )
        await state.set_state(AddByCode.waiting_code)
        lines = [
            f"Секретные числа отправлены на {len(waiting_entries)} номер(ов).",
            "Отправь коды с новой строки в том же порядке:",
            "",
        ]
        lines.extend(f"{index}. {entry['phone']}" for index, entry in enumerate(waiting_entries, start=1))
        if added:
            lines.extend(["", f"Уже добавлено авторизованных: {len(added)}"])
        if errors:
            lines.extend(["", "Ошибки отправки:", *errors[:10]])
        await message.answer("\n".join(lines))
        return

    phone = phones[0]
    runtime = _code_login_runtime(config)
    admin_id = message.from_user.id if message.from_user else 0
    login_id = secrets.token_hex(4)
    phone_digits = phone.lstrip("+")
    session_path = unique_path(config.temp_dir, f"temp_session_{admin_id}_{phone_digits}_{login_id}.session")
    try:
        code_request = await send_code(
            session_path,
            phone,
            config.telegram_api_id,
            config.telegram_api_hash,
            runtime,
        )
    except Exception as exc:
        await state.clear()
        await message.answer(
            f"Не удалось отправить секретное число: {exc}",
            reply_markup=add_account_menu(),
        )
        return

    await state.update_data(
        phone=phone,
        phone_code_hash=code_request.phone_code_hash,
        session_path=str(session_path),
        login_id=login_id,
        admin_id=admin_id,
        runtime=runtime,
        login_started_at=utc_now_iso(),
        code="",
    )
    if code_request.already_authorized and code_request.user:
        await finalize_code_login(message, state, config, twofa=None, user=code_request.user)
        return
    if code_request.already_authorized:
        await state.clear()
        await message.answer(
            "Сессия уже авторизована, но Telegram не вернул данные аккаунта.",
            reply_markup=add_account_menu(),
        )
        return

    await state.set_state(AddByCode.waiting_code)
    await message.answer("Секретное число отправлено. Введи секретное число одним сообщением.")


@router.message(AddByCode.waiting_code)
async def add_by_code_message_code(message: Message, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    bulk_logins = list(data.get("bulk_logins") or [])
    if bulk_logins:
        codes, parse_error = _parse_bulk_login_codes(message.text or "", len(bulk_logins))
        if parse_error:
            await message.answer(parse_error)
            return
        await complete_bulk_codes(message, state, config, codes)
        return
    code = _normalize_login_code(message.text or "")
    await complete_code(message, state, config, code)

@router.callback_query(AddByCode.waiting_code, F.data.startswith("code:"))
async def add_by_code_digit(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await callback.answer("\u0412\u0432\u0435\u0434\u0438 \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u043e\u0431\u044b\u0447\u043d\u044b\u043c \u0442\u0435\u043a\u0441\u0442\u043e\u043c.", show_alert=True)


async def complete_code(message: Message, state: FSMContext, config: Config, code: str) -> None:
    code = _normalize_login_code(code)
    if not code:
        await message.answer("Секретное число пустое.")
        return
    if not 5 <= len(code) <= 8:
        await message.answer("Секретное число должно быть длиной 5-8 цифр. Введи заново.")
        return

    data = await state.get_data()
    phone_code_hash = data.get("phone_code_hash")
    if not phone_code_hash:
        await message.answer("Не удалось сохранить запрос. Начни заново.")
        return
    try:
        user = await sign_in_code(
            Path(data["session_path"]),
            data["phone"],
            code,
            phone_code_hash,
            config.telegram_api_id,
            config.telegram_api_hash,
            data["runtime"],
        )
    except SessionPasswordNeededError:
        await state.update_data(code=code)
        await state.set_state(AddByCode.waiting_twofa)
        await message.answer("Нужен пароль 2FA. Отправь пароль.")
        return
    except Exception as exc:
        logger.exception("Code login sign_in failed")
        await state.update_data(code="")
        await message.answer(f"Вход не удался: {exc}\nВведи секретное число заново.")
        return

    await finalize_code_login(message, state, config, twofa=None, user=user)


@router.message(AddByCode.waiting_twofa)
async def add_by_code_twofa(message: Message, state: FSMContext, config: Config) -> None:
    password = message.text or ""
    data = await state.get_data()
    try:
        user = await sign_in_password(
            Path(data["session_path"]),
            password,
            config.telegram_api_id,
            config.telegram_api_hash,
            data["runtime"],
        )
    except Exception as exc:
        logger.exception("Code login 2FA failed")
        await message.answer(f"Проверка 2FA не прошла: {exc}")
        return
    await finalize_code_login(message, state, config, twofa=password, user=user)


@router.callback_query(F.data == "accounts:add:zip")
async def add_zip_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddByZip.waiting_zip)
    await callback.message.edit_text("Загрузи .zip архив с .session и .json файлами.")
    await callback.answer()


@router.message(AddByZip.waiting_zip, F.document)
async def add_zip_file(message: Message, bot: Bot, state: FSMContext, config: Config) -> None:
    filename = message.document.file_name or ""
    if not filename.lower().endswith(".zip"):
        await message.answer("Нужен файл с расширением .zip.")
        return

    zip_path = unique_path(config.temp_dir, filename)
    await download_document(bot, message.document, zip_path)
    progress_message = await message.answer("ZIP получен. Начинаю импорт...")
    last_progress_update = 0.0

    async def update_zip_progress(done: int, total: int, current: str) -> None:
        nonlocal last_progress_update
        now = time.monotonic()
        if done not in {0, total} and done % 5 != 0 and now - last_progress_update < 3:
            return
        last_progress_update = now
        current_line = f"\nСейчас: {current}" if current else ""
        try:
            await progress_message.edit_text(
                f"Импорт ZIP...\nГотово: {done}/{total}{current_line}"
            )
        except Exception:
            pass

    try:
        results, summary = await import_zip(
            config,
            zip_path=zip_path,
            created_by=message.from_user.id if message.from_user else None,
            progress=update_zip_progress,
        )
    except Exception as exc:
        await state.clear()
        try:
            await progress_message.edit_text(f"Импорт ZIP не удался: {exc}")
        except Exception:
            pass
        await message.answer(
            f"Импорт ZIP не удался: {exc}",
            reply_markup=add_account_menu(),
        )
        return

    lines = [summary, "", "Добавлено в общее хранилище", ""]
    for result in results[:20]:
        if result.account_id:
            lines.append(f"#{result.account_id} | {result.status} | {result.phone or result.username or '-'}")
        else:
            lines.append(f"ERROR | {result.note or 'unknown error'}")
    if len(results) > 20:
        lines.append(f"...и еще {len(results) - 20}")
    await state.clear()
    try:
        await progress_message.edit_text("Импорт ZIP завершён.")
    except Exception:
        pass
    await message.answer("\n".join(lines), reply_markup=accounts_menu())


@router.message(F.text)
async def trigger_account_giveout(message: Message, state: FSMContext, bot: Bot, config: Config) -> None:
    current_state = await state.get_state()
    if current_state is not None:
        return
    if not _is_trigger_message(message, config):
        return
    if (message.text or "").strip() == "/cancel":
        return
    await _give_out_accounts(message, bot, config)


@router.callback_query(F.data.startswith("accounts:page:"))
async def show_accounts_page(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await state.clear()
    _, _, origin, raw_ref, raw_page = callback.data.split(":", 4)
    await _show_account_page(callback, config, origin, int(raw_ref), int(raw_page))


@router.callback_query(F.data.startswith("accounts:reg_filter:"))
async def show_registration_filter(callback: CallbackQuery) -> None:
    origin = callback.data.split(":", 2)[-1]
    if parse_reg_origin(origin) is None:
        await callback.answer("Фильтр доступен только в разделе РЕГ.", show_alert=True)
        return
    await callback.message.edit_text(
        "Фильтр РЕГ\n\n"
        "Можно смотреть аккаунты, которые регались в конкретном сервисе, "
        "или наоборот исключить этот сервис.",
        reply_markup=registration_filter_menu(origin),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:phone:"))
async def send_account_phone(callback: CallbackQuery, config: Config) -> None:
    account_id = int(callback.data.rsplit(":", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if not account.phone:
        await callback.answer("Номер не указан.", show_alert=True)
        return
    await callback.message.answer(_copyable(account.phone))
    await callback.answer("Номер отправлен.")


@router.callback_query(F.data.startswith("account:check_ask:"))
async def ask_check_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        (
            "Проверить аккаунт?\n\n"
            "Бот только подключится к существующей session и проверит авторизацию. "
            "Новое секретное число запрашиваться не будет."
        ),
        reply_markup=confirm_check_account_menu(account.id, origin, int(raw_ref), int(raw_page)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:check_confirm:"))
async def confirm_check_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account_id = int(raw_account_id)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    result = await check_account_validity(account, config)
    status = result["account_status"]
    user = result["user"]
    note = result["reason"]
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    text = _account_detail_text(account)
    if status == "active" and user:
        fields = user_fields(user)
        text += (
            "\n\nПроверка: <b>живой</b>"
            f"\nTelegram ID: {_copyable(fields['telegram_user_id'])}"
            f"\nUsername: {_username(fields['username'])}"
        )
    elif status == "unauthorized":
        text += "\n\nПроверка: <b>не авторизован</b>"
    elif status == "empty":
        text += "\n\nПроверка: <b>Telegram не вернул данные аккаунта</b>"
    elif status == "twofa_required":
        text += "\n\nПроверка: <b>требуется 2FA</b>"
    elif status == "missing_file":
        text += "\n\nПроверка: <b>пропущен</b>\nSession файл не найден."
    elif status == "bad_json":
        text += f"\n\nПроверка: <b>пропущен</b>\n{_text(note)}"
    else:
        text += f"\n\nПроверка: <b>ошибка</b>\n{_text(note)}"

    await callback.message.edit_text(
        text,
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=int(raw_ref),
            page=int(raw_page),
        ),
    )
    await callback.answer("Проверка завершена.")

async def _send_account_code(callback: CallbackQuery, config: Config) -> None:
    account_id = int(callback.data.rsplit(":", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    session_path = Path(account.session_path)
    if not session_path.exists():
        await callback.answer("Session файл не найден.", show_alert=True)
        return
    try:
        api_id, api_hash, runtime = _account_connection_params(account, config)
        code = await get_latest_telegram_code(session_path, api_id, api_hash, runtime)
        title = "Секретное число"
        not_found = "Секретное число не найдено в последних сообщениях."
    except Exception as exc:
        await callback.message.answer(f"Не удалось получить секретное число: {escape(str(exc))}")
        await callback.answer()
        return

    if not code:
        await callback.message.answer(not_found)
        await callback.answer()
        return

    await callback.message.answer(f"{title}: <code>{escape(code)}</code>")
    await callback.answer("Секретное число найдено.")


@router.callback_query(F.data.startswith("account:request_code:"))
@router.callback_query(F.data.startswith("arc:"))
async def request_account_code(callback: CallbackQuery, config: Config, bot: Bot) -> None:
    parts = callback.data.split(":")
    is_short_callback = parts[0] == "arc"
    if len(parts) < 5:
        await callback.answer("\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0437\u0430\u043f\u0440\u043e\u0441.", show_alert=True)
        return
    try:
        offset = 1 if is_short_callback else 2
        account_id = int(parts[offset])
        expected_chat_id = int(parts[offset + 1])
        expected_user_id = int(parts[offset + 2])
        expected_reply_to_message_id = int(parts[offset + 3]) if len(parts) > offset + 3 else None
        if expected_reply_to_message_id == 0:
            expected_reply_to_message_id = None
    except ValueError:
        await callback.answer("\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0437\u0430\u043f\u0440\u043e\u0441.", show_alert=True)
        return
    actual_chat_id = callback.message.chat.id if callback.message else None
    actual_user_id = callback.from_user.id if callback.from_user else None
    if actual_chat_id != expected_chat_id or actual_user_id != expected_user_id:
        await callback.answer("\u042d\u0442\u0430 \u043a\u043d\u043e\u043f\u043a\u0430 \u043f\u0440\u0435\u0434\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0430 \u0434\u0440\u0443\u0433\u043e\u043c\u0443 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044e.", show_alert=True)
        return
    task_key = (account_id, expected_chat_id, expected_user_id)
    if task_key in _active_retry_code_tasks:
        await callback.answer("\u0423\u0436\u0435 \u0438\u0449\u0443 \u043d\u043e\u0432\u043e\u0435 \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u043f\u043e \u044d\u0442\u043e\u043c\u0443 \u043d\u043e\u043c\u0435\u0440\u0443.", show_alert=True)
        return
    account = get_account(config, account_id)
    if not account:
        await callback.answer("\u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", show_alert=True)
        return
    session_path = Path(account.session_path)
    if not session_path.exists():
        await callback.answer("\u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d.", show_alert=True)
        return

    await callback.answer("\u0418\u0449\u0443 \u043d\u043e\u0432\u043e\u0435 \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e 2 \u043c\u0438\u043d\u0443\u0442\u044b...")
    _active_retry_code_tasks.add(task_key)
    try:
        current, code_message, terminal = await _find_new_telegram_code_message(
            config,
            account,
            started_at=datetime.now(timezone.utc),
            deadline=time.monotonic() + CODE_RETRY_WATCH_SECONDS,
            seen_keys=set(),
        )
    finally:
        _active_retry_code_tasks.discard(task_key)

    if not current:
        await callback.message.answer("\u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.", reply_to_message_id=expected_reply_to_message_id)
        return
    if terminal:
        await callback.message.answer("\u0410\u043a\u043a\u0430\u0443\u043d\u0442 \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0434\u043b\u044f \u043f\u043e\u0438\u0441\u043a\u0430 \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u044b\u0445 \u0447\u0438\u0441\u0435\u043b.", reply_to_message_id=expected_reply_to_message_id)
        return
    if not code_message:
        await callback.message.answer("\u041d\u043e\u0432\u043e\u0435 \u0441\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u0437\u0430 2 \u043c\u0438\u043d\u0443\u0442\u044b \u043d\u0435 \u043f\u0440\u0438\u0448\u043b\u043e.", reply_to_message_id=expected_reply_to_message_id)
        return
    try:
        await _send_code_delivery(
            bot,
            chat_id=expected_chat_id,
            account=current,
            code=code_message.code,
            requester_user_id=expected_user_id,
            reply_to_message_id=expected_reply_to_message_id,
        )
    except Exception as exc:
        logger.warning("Cannot send retry Telegram code for account %s: %s", account_id, exc)
        await callback.message.answer(f"\u0421\u0435\u043a\u0440\u0435\u0442\u043d\u043e\u0435 \u0447\u0438\u0441\u043b\u043e \u043f\u0440\u0438\u0448\u043b\u043e, \u043d\u043e \u0431\u043e\u0442 \u043d\u0435 \u0441\u043c\u043e\u0433 \u0435\u0433\u043e \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c: {escape(str(exc))}")

@router.callback_query(F.data.startswith("account:telegram_code:"))
async def send_telegram_code(callback: CallbackQuery, config: Config) -> None:
    await _send_account_code(callback, config)



@router.callback_query(F.data.startswith("account:open:"))
async def show_account_detail_callback(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=int(raw_ref),
            page=int(raw_page),
        ),
    )
    await callback.answer()


@router.message(F.text.regexp(r"^/account_\d+$"))
async def show_account_detail_message(message: Message, config: Config) -> None:
    account_id = int((message.text or "").rsplit("_", 1)[-1])
    account = get_account(config, account_id)
    if not account:
        await message.answer("Аккаунт не найден.")
        return
    origin = _origin_for_account(account)
    await message.answer(
        _account_detail_text(account),
        reply_markup=account_detail_menu(account.id, account_stage=account.account_stage, origin=origin, ref_id=0, page=0),
    )


@router.callback_query(F.data.startswith("accounts:file:"))
async def download_account_file(callback: CallbackQuery, config: Config) -> None:
    _, _, file_type, raw_id = callback.data.split(":", 3)
    account = get_account(config, int(raw_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return

    path = Path(account.session_path) if file_type == "session" else Path(account.json_original_path or account.json_effective_path or "")
    if not path.exists():
        await callback.answer("Файл не найден.", show_alert=True)
        return

    await callback.message.answer_document(FSInputFile(path))
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:zip_common:"))
async def download_common_stage_zip(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    registration_service: str | None = None
    excluded_service: str | None = None
    if stage == "clean":
        accounts = _clean_issueable_accounts(config)
    elif stage == "issued":
        accounts = list_accounts_by_scope(config, account_stage="issued")
    else:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    if not accounts:
        await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
        return
    zip_path, files_count = _make_accounts_zip(config, accounts, stage)
    if files_count == 0:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        await callback.answer("Файлы для архива не найдены.", show_alert=True)
        return
    await callback.message.answer_document(
        FSInputFile(zip_path),
        caption=f"Хранилище {_stage_filter_title(stage, registration_service, excluded_service)}: {len(accounts)} аккаунтов, {files_count} файлов.",
    )
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    await callback.answer("ZIP сформирован.")


@router.callback_query(F.data.startswith("account:delete_ask:"))
async def ask_delete_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Удаление здесь недоступно.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        "ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ УДАЛИТЬ АККАУНТ И ЕГО ФАЙЛЫ С СЕРВЕРА?",
        reply_markup=confirm_delete_account_menu(account.id, origin, int(raw_ref), int(raw_page)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:delete_confirm:"))
async def confirm_delete_account(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, origin, _raw_ref, _raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Удаление здесь недоступно.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    stage = account.account_stage
    removed_files = _delete_local_account_files(config, [account])
    delete_account_row(config, account.id)
    clean_count, issued_count = _common_account_counts(config)
    await callback.message.edit_text(
        (
            f"Аккаунт #{account.id} удален из бота.\n"
            f"Файлов удалено с сервера: {removed_files}\n\n"
            f"Хранилище\nЧистые: {clean_count}\nВыданные: {issued_count}"
        ),
        reply_markup=common_storage_sections_menu(clean_count=clean_count, issued_count=issued_count),
    )
    await callback.answer(f"Удалено из {_stage_title(stage)}.")


@router.callback_query(F.data.startswith("accounts:delete_common_ask:"))
async def ask_delete_common_stage(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    registration_service: str | None = None
    excluded_service: str | None = None
    if stage == "clean":
        total = len(_clean_issueable_accounts(config))
    elif stage == "issued":
        total = count_accounts_by_stage(config, account_stage="issued")
    else:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    if not total:
        await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
        return
    await callback.message.edit_text(
        (
            f"ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ УДАЛИТЬ ВЕСЬ {_stage_filter_title(stage, registration_service, excluded_service)}?\n"
            f"Аккаунтов: {total}\nФайлы будут удалены только с сервера."
        ),
        reply_markup=confirm_delete_common_stage_menu(stage, registration_service, excluded_service),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("accounts:delete_common_confirm:"))
async def confirm_delete_common_stage(callback: CallbackQuery, config: Config) -> None:
    parts = callback.data.split(":")
    stage = parts[2] if len(parts) > 2 else ""
    raw_filter = parts[3] if len(parts) > 3 else "all"
    registration_service: str | None = None
    excluded_service: str | None = None
    if stage == "clean":
        accounts = _clean_issueable_accounts(config)
        if not accounts:
            await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
            return
        removed_files = _delete_local_account_files(config, accounts)
        removed_rows = delete_accounts_by_stage(config, excluded_account_stage=("issued", "processing"))
    elif stage == "issued":
        accounts = list_accounts_by_scope(config, account_stage="issued")
        if not accounts:
            await callback.answer("В этом разделе нет аккаунтов.", show_alert=True)
            return
        removed_files = _delete_local_account_files(config, accounts)
        removed_rows = delete_accounts_by_stage(config, account_stage="issued")
    else:
        await callback.answer("Неизвестный раздел.", show_alert=True)
        return
    clean_count, issued_count = _common_account_counts(config)
    await callback.message.edit_text(
        (
            f"{_stage_filter_title(stage, None, None)} очищен.\n"
            f"Аккаунтов удалено из бота: {removed_rows}\n"
            f"Файлов удалено с сервера: {removed_files}\n\n"
            f"Хранилище\nЧистые: {clean_count}\nВыданные: {issued_count}"
        ),
        reply_markup=common_storage_sections_menu(clean_count=clean_count, issued_count=issued_count),
    )
    await callback.answer("Раздел очищен.")


@router.callback_query(F.data.startswith("account:service:"))
async def choose_registration_service(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    _, _, raw_account_id, origin, raw_ref, raw_page = callback.data.split(":", 5)
    if not _origin_is_valid(origin):
        await callback.answer("Этот раздел больше недоступен.", show_alert=True)
        return
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    selected_services = list(services_from_storage(account.registration_services, account.registration_service))
    await state.update_data(
        service_edit_account_id=account.id,
        service_edit_origin=origin,
        service_edit_ref_id=int(raw_ref),
        service_edit_page=int(raw_page),
        service_edit_selected=selected_services,
    )
    await callback.message.edit_text(
        _service_selection_text(selected_services),
        reply_markup=registration_service_menu(selected_services),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:service_toggle:"))
async def toggle_registration_service(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, service = callback.data.split(":", 2)
    if not is_registration_service(service):
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    data = await state.get_data()
    if "service_edit_account_id" not in data:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    selected_services = list(data.get("service_edit_selected") or [])
    if service in selected_services:
        selected_services.remove(service)
    else:
        selected_services.append(service)
    await state.update_data(service_edit_selected=selected_services)
    await callback.message.edit_text(
        _service_selection_text(selected_services),
        reply_markup=registration_service_menu(selected_services),
    )
    await callback.answer()


@router.callback_query(F.data == "account:service_save")
async def save_registration_services(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    account_id = data.get("service_edit_account_id")
    if account_id is None:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    selected_services = list(data.get("service_edit_selected") or [])
    if not selected_services:
        await callback.answer("Выбери хотя бы один сервис.", show_alert=True)
        return
    account = get_account(config, int(account_id))
    if not account:
        await state.clear()
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    set_account_stage(config, int(account_id), "reg", registration_services=selected_services)
    account = get_account(config, int(account_id))
    await state.clear()
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    new_origin = _origin_for_account(account)
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=new_origin,
            ref_id=0,
            page=0,
        ),
    )
    await callback.answer(f"Сервисы сохранены: {services_label(account.registration_services, account.registration_service)}")


@router.callback_query(F.data == "account:service_cancel")
async def cancel_registration_services(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    data = await state.get_data()
    account_id = data.get("service_edit_account_id")
    origin = data.get("service_edit_origin") or "common_reg"
    ref_id = int(data.get("service_edit_ref_id") or 0)
    page = int(data.get("service_edit_page") or 0)
    await state.clear()
    if account_id is None:
        await callback.answer("Открой карточку аккаунта заново.", show_alert=True)
        return
    account = get_account(config, int(account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=origin,
            ref_id=ref_id,
            page=page,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_ask1:"))
async def ask_account_stage_first(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, origin, raw_ref, raw_page = callback.data.split(":", 6)
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg":
        await callback.answer("Сначала выбери сервисы.", show_alert=True)
        return
    else:
        text = "ВЫ УВЕРЕНЫ ЧТО ХОТИТЕ ПЕРЕНЕСТИ АКК В НЕРЕГ?"
    await callback.message.edit_text(
        text,
        reply_markup=confirm_account_stage_menu(
            account.id,
            target_stage,
            None,
            origin,
            int(raw_ref),
            int(raw_page),
            step=1,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_ask2:"))
async def ask_account_stage_second(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, raw_service, origin, raw_ref, raw_page = callback.data.split(":", 7)
    registration_service = None if raw_service == "none" else raw_service
    account = get_account(config, int(raw_account_id))
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg":
        if not is_registration_service(registration_service):
            await callback.answer("Неизвестный сервис.", show_alert=True)
            return
        text = f"ПОДТВЕРДИТЕ ЕЩЕ РАЗ: ПОМЕТИТЬ АКК РЕГАННЫМ?\nСервис: {service_label(registration_service)}"
    else:
        text = "ПОДТВЕРДИТЕ ЕЩЕ РАЗ: ПЕРЕНЕСТИ АКК В НЕРЕГ?"
    await callback.message.edit_text(
        text,
        reply_markup=confirm_account_stage_menu(
            account.id,
            target_stage,
            registration_service,
            origin,
            int(raw_ref),
            int(raw_page),
            step=2,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("account:stage_confirm:"))
async def confirm_account_stage(callback: CallbackQuery, config: Config) -> None:
    _, _, raw_account_id, target_stage, raw_service, _origin, _raw_ref, _raw_page = callback.data.split(":", 7)
    registration_service = None if raw_service == "none" else raw_service
    account_id = int(raw_account_id)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    if target_stage == "reg" and not is_registration_service(registration_service):
        await callback.answer("Неизвестный сервис.", show_alert=True)
        return
    set_account_stage(config, account_id, target_stage, registration_service)
    account = get_account(config, account_id)
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    new_origin = _origin_for_account(account)
    await callback.message.edit_text(
        _account_detail_text(account),
        reply_markup=account_detail_menu(
            account.id,
            account_stage=account.account_stage,
            origin=new_origin,
            ref_id=0,
            page=0,
        ),
    )
    if target_stage == "reg":
        done = f"Аккаунт перенесен в РЕГ {service_label(registration_service)}."
    else:
        done = "Аккаунт перенесен в НЕРЕГ."
    await callback.answer(done)
