from dataclasses import dataclass
from pathlib import Path
import os
import re

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    telegram_api_id: int
    telegram_api_hash: str
    telegram_proxy: str | None
    bot_parse_mode: str
    data_dir: Path
    sessions_dir: Path
    json_dir: Path
    temp_dir: Path
    db_path: Path
    default_lang_code: str
    default_system_lang_code: str
    default_lang_pack: str
    trigger_chat_id: int | None
    trigger_word: str = "\u0442\u0433"


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("PUT_"):
        raise RuntimeError(f"Set {name} in .env")
    return value


def _parse_admin_ids(raw: str, *, source: str) -> frozenset[int]:
    ids: set[int] = set()
    for item in re.split(r"[\s,;]+", raw):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            raise RuntimeError(f"{source} must contain only numeric Telegram IDs")
        ids.add(int(item))
    if not ids:
        raise RuntimeError(f"Set {source} in .env")
    return frozenset(ids)


def _get_admin_ids() -> frozenset[int]:
    """Load persistent admins and optional immutable owners from .env."""
    admin_raw = os.getenv("ADMIN_IDS", "").strip()
    owner_raw = os.getenv("OWNER_IDS", "").strip()
    admin_ids = (
        _parse_admin_ids(admin_raw, source="ADMIN_IDS")
        if admin_raw and not admin_raw.startswith("PUT_")
        else frozenset()
    )
    owner_ids = (
        _parse_admin_ids(owner_raw, source="OWNER_IDS")
        if owner_raw and not owner_raw.startswith("PUT_")
        else frozenset()
    )
    if not admin_ids and not owner_ids:
        raise RuntimeError("Set ADMIN_IDS in .env")
    return admin_ids | owner_ids


def _write_env_value(name: str, value: str) -> None:
    path = Path(".env")
    line = f"{name}={value}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for index, existing in enumerate(lines):
        if existing.startswith(f"{name}="):
            lines[index] = line
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_admin_ids(config: Config, admin_ids: set[int] | frozenset[int]) -> None:
    """Persist the managed admin list and apply it without a bot restart."""
    normalized = frozenset(int(user_id) for user_id in admin_ids if int(user_id) > 0)
    owner_raw = os.getenv("OWNER_IDS", "").strip()
    owner_ids = (
        _parse_admin_ids(owner_raw, source="OWNER_IDS")
        if owner_raw and not owner_raw.startswith("PUT_")
        else frozenset()
    )
    normalized |= owner_ids
    if not normalized:
        raise ValueError("At least one administrator is required")
    _write_env_value("ADMIN_IDS", ",".join(str(user_id) for user_id in sorted(normalized)))
    object.__setattr__(config, "admin_ids", normalized)


def _get_optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)


def load_config() -> Config:
    load_dotenv()

    data_dir = Path(os.getenv("DATA_DIR", "storage"))
    return Config(
        bot_token=_get_required("BOT_TOKEN"),
        admin_ids=_get_admin_ids(),
        telegram_api_id=int(_get_required("TELEGRAM_API_ID")),
        telegram_api_hash=_get_required("TELEGRAM_API_HASH"),
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip() or None,
        bot_parse_mode=os.getenv("BOT_PARSE_MODE", "HTML"),
        data_dir=data_dir,
        sessions_dir=Path(os.getenv("SESSIONS_DIR", data_dir / "sessions")),
        json_dir=Path(os.getenv("JSON_DIR", data_dir / "json")),
        temp_dir=Path(os.getenv("TEMP_DIR", data_dir / "tmp")),
        db_path=Path(os.getenv("DB_PATH", data_dir / "bot.sqlite3")),
        default_lang_code=os.getenv("DEFAULT_LANG_CODE", "en"),
        default_system_lang_code=os.getenv("DEFAULT_SYSTEM_LANG_CODE", "en-US"),
        default_lang_pack=os.getenv("DEFAULT_LANG_PACK", "tdesktop"),
        trigger_chat_id=_get_optional_int("TRIGGER_CHAT_ID"),
        trigger_word=os.getenv("TRIGGER_WORD", "\u0442\u0433").strip() or "\u0442\u0433",
    )
