from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Any

from tglol.config import Config


WINDOWS_DEVICES = [
    "Desktop",
    "PC",
    "Dell XPS 13 9310",
    "Dell XPS 15 9500",
    "Dell Latitude 5420",
    "Dell Latitude 5520",
    "Dell OptiPlex 7090",
    "HP EliteBook 840 G8",
    "HP ProBook 450 G8",
    "Lenovo ThinkPad T14",
    "Lenovo ThinkPad T15",
    "Lenovo ThinkPad X1 Carbon",
    "Lenovo IdeaCentre 5",
    "ASUS ZenBook 14",
    "ASUS VivoBook 15",
]

WINDOWS_SDKS = [
    "Windows 11 x64",
    "Windows 11 Pro x64",
    "Windows 11 Home x64",
    "Windows 10 x64",
    "Windows 10 Pro x64",
]

APP_VERSIONS = [
    "7.0.6 x64",
]


def random_desktop_runtime() -> dict[str, str]:
    device = random.choice(WINDOWS_DEVICES)
    sdk = random.choice(WINDOWS_SDKS)

    return {
        "device": device,
        "sdk": sdk,
        "app_version": random.choice(APP_VERSIONS),
        "lang_code": "en",
        "system_lang_code": "en-US",
        "lang_pack": "tdesktop",
    }



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def random_created_at_iso() -> str:
    offset = timedelta(
        days=random.randint(0, 45),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return (datetime.now(timezone.utc) - offset).isoformat(timespec="seconds")


def generated_account_json(
    config: Config,
    *,
    runtime: dict[str, str] | None = None,
    twofa: str | None = None,
    session_file: str | None = None,
    phone: str | None = None,
    user_id: int | None = None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, Any]:
    runtime = runtime or random_desktop_runtime()
    return {
        "app_id": config.telegram_api_id,
        "app_hash": config.telegram_api_hash,
        "device": runtime["device"],
        "sdk": runtime["sdk"],
        "app_version": runtime["app_version"],
        "lang_code": config.default_lang_code,
        "system_lang_code": config.default_system_lang_code,
        "lang_pack": config.default_lang_pack,
        "proxy": runtime.get("proxy"),
        "twoFA": twofa,
        "phone": phone,
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "session_file": session_file,
        "created_at": random_created_at_iso(),
    }
