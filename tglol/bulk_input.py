from __future__ import annotations

import re


def _normalize_login_phone(raw: str) -> str | None:
    raw = (raw or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if raw.startswith("00") and len(digits) > 2:
        digits = digits[2:]
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def _is_import_line(line: str) -> bool:
    stripped = line.strip()
    return bool(re.fullmatch(r"[+\d\s\-()]+", stripped)) and any(ch.isdigit() for ch in stripped)


def _clean_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if _is_import_line(line)]


def parse_bulk_phone_input(text: str) -> list[str] | None:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    lines = _clean_lines(lines)
    phones: list[str] = []
    for line in lines:
        phone = _normalize_login_phone(line)
        if not phone:
            return None
        phones.append(phone)
    return phones if phones else None
