from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import socket
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    import socks
except ImportError:  # pragma: no cover - exercised only without dependency installed
    socks = None

from telethon.network.connection.tcpmtproxy import ConnectionTcpMTProxyRandomizedIntermediate


TELEGRAM_CONNECT_CHECK_HOST = "149.154.167.50"
TELEGRAM_CONNECT_CHECK_PORT = 443


PROXY_TYPE_ALIASES = {
    "socks": "socks5",
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "socks4a": "socks4",
    "http": "http",
    "https": "http",
    "mtproto": "mtproto",
}


@dataclass(frozen=True)
class ProxySettings:
    kind: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    secret: str | None = None
    rdns: bool = True
    transport: str | None = None

    @property
    def is_mtproto(self) -> bool:
        return self.kind == "mtproto"


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = unquote(str(value)).strip()
    return value or None


def _normalize_kind(value: str | None) -> str:
    raw = (value or "socks5").lower().strip()
    kind = PROXY_TYPE_ALIASES.get(raw)
    if not kind:
        raise ValueError(f"unsupported proxy type: {value}")
    return kind


def _normalize_host(value: str | None) -> str:
    host = (value or "").strip().strip("[]")
    if not host:
        raise ValueError("proxy host is required")
    return host


def _normalize_port(value: str | int | None) -> int:
    try:
        port = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("proxy port must be a number") from exc
    if not 1 <= port <= 65535:
        raise ValueError("proxy port must be between 1 and 65535")
    return port


def _normalize_secret(value: str | None) -> tuple[str, str]:
    secret = _clean_optional(value)
    if not secret:
        raise ValueError("MTProto proxy secret is required")
    secret = secret.lower()
    if re.fullmatch(r"[0-9a-f]{32}", secret):
        return secret, "intermediate"
    if re.fullmatch(r"dd[0-9a-f]{32}", secret):
        return secret, "randomized_intermediate"
    if re.fullmatch(r"ee[0-9a-f]{32}.*", secret):
        return secret, "tls"
    return secret, "auto"


def normalize_proxy(
    *,
    kind: str | None = None,
    host: str | None = None,
    port: str | int | None = None,
    username: str | None = None,
    password: str | None = None,
    secret: str | None = None,
    rdns: bool = True,
) -> ProxySettings:
    normalized_kind = _normalize_kind(kind)
    normalized_host = _normalize_host(host)
    normalized_port = _normalize_port(port)
    if normalized_kind == "mtproto":
        normalized_secret, transport = _normalize_secret(secret)
        return ProxySettings(
            kind="mtproto",
            host=normalized_host,
            port=normalized_port,
            secret=normalized_secret,
            rdns=False,
            transport=transport,
        )
    return ProxySettings(
        kind=normalized_kind,
        host=normalized_host,
        port=normalized_port,
        username=_clean_optional(username),
        password=_clean_optional(password),
        rdns=bool(rdns),
    )


def _query_value(query: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = query.get(key)
        if values:
            return values[0]
    return None


def _parse_tg_proxy_url(value: str) -> ProxySettings | None:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path.strip("/").lower()
    if scheme == "tg":
        proxy_type = parsed.netloc.lower()
    elif scheme in {"http", "https"} and host in {"t.me", "telegram.me"}:
        proxy_type = path
    else:
        return None
    query = parse_qs(parsed.query)
    if proxy_type == "socks":
        return normalize_proxy(
            kind="socks5",
            host=_query_value(query, "server", "host"),
            port=_query_value(query, "port"),
            username=_query_value(query, "user", "username", "login"),
            password=_query_value(query, "pass", "password"),
        )
    if proxy_type == "proxy":
        return normalize_proxy(
            kind="mtproto",
            host=_query_value(query, "server", "host"),
            port=_query_value(query, "port"),
            secret=_query_value(query, "secret"),
        )
    return None


def _split_plain_proxy(value: str) -> list[str]:
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            host = value[1:end]
            rest = value[end + 1 :].lstrip(":")
            return [host, *rest.split(":")]
    return value.split(":")


def _kind_from_numeric(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return {1: "socks4", 2: "socks5", 3: "http"}.get(number)


def _parse_proxy_mapping(value: dict[str, Any]) -> ProxySettings | None:
    if not value:
        return None
    if "secret" in value or str(value.get("type") or value.get("kind") or "").lower() == "mtproto":
        return normalize_proxy(
            kind="mtproto",
            host=value.get("host") or value.get("addr") or value.get("server") or value.get("proxy_host"),
            port=value.get("port") or value.get("proxy_port"),
            secret=value.get("secret"),
        )
    raw_kind = value.get("kind") or value.get("type") or value.get("proxy_type") or value.get("scheme")
    kind = _kind_from_numeric(raw_kind) or raw_kind
    return normalize_proxy(
        kind=kind,
        host=value.get("host") or value.get("addr") or value.get("server") or value.get("proxy_host"),
        port=value.get("port") or value.get("proxy_port"),
        username=value.get("username") or value.get("user") or value.get("login"),
        password=value.get("password") or value.get("pass"),
        rdns=bool(value.get("rdns", True)),
    )


def _parse_proxy_sequence(value: list[Any] | tuple[Any, ...]) -> ProxySettings | None:
    parts = list(value)
    if not parts:
        return None
    if len(parts) == 3:
        return normalize_proxy(kind="mtproto", host=parts[0], port=parts[1], secret=parts[2])
    if len(parts) in {5, 6}:
        raw_kind, host, port = parts[0], parts[1], parts[2]
        kind = _kind_from_numeric(raw_kind) or raw_kind
        if len(parts) == 5:
            username, password = parts[3], parts[4]
            rdns = True
        else:
            fourth = parts[3]
            if isinstance(fourth, bool):
                rdns, username, password = fourth, parts[4], parts[5]
            else:
                username, password, rdns = parts[3], parts[4], parts[5]
        return normalize_proxy(kind=kind, host=host, port=port, username=username, password=password, rdns=bool(rdns))
    raise ValueError("unsupported proxy sequence format")


def parse_proxy(value: str | dict[str, Any] | list[Any] | tuple[Any, ...] | ProxySettings | None) -> ProxySettings | None:
    if value is None or isinstance(value, ProxySettings):
        return value
    if isinstance(value, dict):
        return _parse_proxy_mapping(value)
    if isinstance(value, (list, tuple)):
        return _parse_proxy_sequence(value)
    raw = str(value).strip()
    if not raw:
        return None

    tg_proxy = _parse_tg_proxy_url(raw)
    if tg_proxy:
        return tg_proxy

    parsed = urlparse(raw)
    if parsed.scheme and "://" in raw:
        kind = _normalize_kind(parsed.scheme)
        if kind == "mtproto":
            return normalize_proxy(kind="mtproto", host=parsed.hostname, port=parsed.port, secret=parsed.password or parsed.username)
        return normalize_proxy(
            kind=kind,
            host=parsed.hostname,
            port=parsed.port,
            username=parsed.username,
            password=parsed.password,
        )

    parts = _split_plain_proxy(raw)
    if len(parts) == 2:
        return normalize_proxy(host=parts[0], port=parts[1])
    if len(parts) == 3:
        return normalize_proxy(kind="mtproto", host=parts[0], port=parts[1], secret=parts[2])
    if len(parts) >= 4:
        return normalize_proxy(host=parts[0], port=parts[1], username=parts[2], password=":".join(parts[3:]))
    raise ValueError("unsupported proxy format")


def mask_proxy(proxy: str | ProxySettings | None) -> str:
    settings = parse_proxy(proxy)
    if settings is None:
        return "none"
    if settings.is_mtproto:
        secret = settings.secret or ""
        masked_secret = f"{secret[:4]}...{secret[-4:]}" if len(secret) > 8 else "***"
        return f"mtproto://{settings.host}:{settings.port}?secret={masked_secret}"
    auth = ""
    if settings.username:
        auth = f"{settings.username}:***@"
    return f"{settings.kind}://{auth}{settings.host}:{settings.port}"


def proxy_to_telethon(proxy: str | ProxySettings | None) -> dict[str, Any]:
    settings = parse_proxy(proxy)
    if settings is None:
        return {}
    if settings.is_mtproto:
        return {
            "connection": ConnectionTcpMTProxyRandomizedIntermediate,
            "proxy": (settings.host, settings.port, settings.secret),
        }
    if socks is None:
        raise RuntimeError("PySocks is required for SOCKS/HTTP proxies")
    type_map = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }
    return {
        "proxy": (
            type_map[settings.kind],
            settings.host,
            settings.port,
            settings.rdns,
            settings.username,
            settings.password,
        )
    }


async def check_proxy(proxy: str | ProxySettings | None, *, timeout: float = 10.0) -> bool:
    settings = parse_proxy(proxy)
    if settings is None:
        return True
    if not settings.is_mtproto:
        if socks is None:
            raise RuntimeError("PySocks is required for SOCKS/HTTP proxies")
        return await asyncio.to_thread(_check_socks_proxy, settings, timeout)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.host, settings.port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _check_socks_proxy(settings: ProxySettings, timeout: float) -> bool:
    type_map = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }
    sock = socks.socksocket()
    sock.settimeout(timeout)
    try:
        sock.set_proxy(
            type_map[settings.kind],
            settings.host,
            settings.port,
            rdns=settings.rdns,
            username=settings.username,
            password=settings.password,
        )
        sock.connect((TELEGRAM_CONNECT_CHECK_HOST, TELEGRAM_CONNECT_CHECK_PORT))
        return True
    except (OSError, socket.timeout):
        return False
    finally:
        sock.close()
