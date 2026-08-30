from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tglol.config import Config


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("корень JSON должен быть объектом")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def pick_api(data: dict[str, Any] | None, config: Config) -> tuple[int, str]:
    data = data or {}
    for id_key, hash_key in (("app_id", "app_hash"), ("api_id", "api_hash")):
        api_id = pick_int(data, id_key)
        api_hash = pick_str(data, hash_key)
        if api_id and api_id > 0 and api_hash:
            return api_id, api_hash
    return config.telegram_api_id, config.telegram_api_hash


def pick_twofa(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    for key in ("twoFA", "twofa_password", "twofa", "two_fa", "password"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def pick_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def pick_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _pick_proxy_mapping(data: dict[str, Any]) -> dict[str, Any] | None:
    host = pick_str(data, "proxy_host", "proxy_addr", "proxy_server", "proxy_ip")
    port = pick_int(data, "proxy_port")
    if not host or port is None:
        return None

    result: dict[str, Any] = {
        "host": host,
        "port": port,
    }
    proxy_type = data.get("proxy_type") or data.get("proxy_kind") or data.get("proxy_scheme")
    if proxy_type not in (None, ""):
        result["type"] = proxy_type
    username = pick_str(data, "proxy_username", "proxy_user", "proxy_login")
    password = pick_str(data, "proxy_password", "proxy_pass")
    secret = pick_str(data, "proxy_secret")
    if username:
        result["username"] = username
    if password:
        result["password"] = password
    if secret:
        result["secret"] = secret
    return result


def runtime_from_json(data: dict[str, Any]) -> dict[str, Any]:
    runtime: dict[str, Any] = {
        "device": pick_str(data, "device_model", "device") or "Desktop",
        "sdk": pick_str(data, "system_version", "sdk", "SDK") or "Windows 11",
        "app_version": pick_str(data, "app_version") or "6.9.3 x64",
        "lang_code": "en",
        "system_lang_code": "en-US",
        "lang_pack": pick_str(data, "lang_pack") or "tdesktop",
    }
    for key in ("proxy", "proxy_url", "proxy_string", "proxy_settings"):
        value = data.get(key)
        if value not in (None, "", [], {}):
            runtime["proxy"] = value
            runtime["proxy_source"] = "json"
            break
    else:
        proxy_mapping = _pick_proxy_mapping(data)
        if proxy_mapping:
            runtime["proxy"] = proxy_mapping
            runtime["proxy_source"] = "json"
    return runtime


def json_identity(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "phone": pick_str(data, "phone", "phone_number"),
        "telegram_user_id": pick_int(data, "user_id", "id"),
        "username": pick_str(data, "username"),
        "first_name": pick_str(data, "first_name"),
        "last_name": pick_str(data, "last_name"),
        "session_file": pick_str(data, "session_file"),
    }
