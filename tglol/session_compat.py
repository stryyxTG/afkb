from __future__ import annotations

import inspect
from pathlib import Path
import shutil
import sqlite3

from telethon.sessions.sqlite import CURRENT_VERSION, SQLiteSession


def telethon_session_columns() -> tuple[str, ...]:
    """Return the sessions table shape expected by the installed Telethon."""
    try:
        source = inspect.getsource(SQLiteSession.__init__)
    except (OSError, TypeError):
        source = ""
    if "tmp_key" in source or "tmp_auth_key" in source:
        return ("dc_id", "server_address", "port", "auth_key", "tmp_auth_key", "takeout_id")
    return ("dc_id", "server_address", "port", "auth_key", "takeout_id")


def telethon_compat_sibling_path(session_path: Path) -> Path:
    session_path = Path(session_path)
    return session_path.with_name(f"{session_path.stem}_telethon{session_path.suffix}")


def telethon_compatible_session_path(session_path: Path) -> Path:
    """Return a Telethon-compatible copy when a session has extra columns."""
    session_path = Path(session_path)
    if not session_path.exists():
        return session_path
    try:
        columns = _session_columns(session_path)
    except sqlite3.Error:
        return session_path

    expected_columns = telethon_session_columns()
    if columns == list(expected_columns):
        _sync_session_version(session_path, expected_columns)
        return session_path
    if _can_upgrade_in_place(columns, expected_columns):
        _rewrite_sessions_table(session_path, expected_columns)
        return session_path
    if len(columns) < len(expected_columns):
        return session_path

    compat_path = telethon_compat_sibling_path(session_path)
    if _compat_is_current(session_path, compat_path, expected_columns):
        _sync_session_version(compat_path, expected_columns)
        return compat_path

    compat_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(session_path, compat_path)
    _rewrite_sessions_table(compat_path, expected_columns)
    return compat_path


def _session_columns(path: Path) -> list[str]:
    connection = sqlite3.connect(str(path))
    try:
        rows = connection.execute("PRAGMA table_info(sessions)").fetchall()
        return [str(row[1]) for row in rows]
    finally:
        connection.close()


def _compat_is_current(source: Path, compat: Path, expected_columns: tuple[str, ...]) -> bool:
    if not compat.exists():
        return False
    try:
        if compat.stat().st_mtime < source.stat().st_mtime:
            return False
        return _session_columns(compat) == list(expected_columns)
    except (OSError, sqlite3.Error):
        return False


def _can_upgrade_in_place(columns: list[str], expected_columns: tuple[str, ...]) -> bool:
    return len(columns) < len(expected_columns) and all(column in expected_columns for column in columns)


def _sync_session_version(path: Path, expected_columns: tuple[str, ...]) -> None:
    if "tmp_auth_key" not in expected_columns:
        return
    connection = sqlite3.connect(str(path))
    try:
        version_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'version'"
        ).fetchone()
        if not version_exists:
            return
        connection.execute("DELETE FROM version")
        connection.execute("INSERT INTO version VALUES (?)", (CURRENT_VERSION,))
        connection.commit()
    finally:
        connection.close()


def _rewrite_sessions_table(path: Path, expected_columns: tuple[str, ...]) -> None:
    connection = sqlite3.connect(str(path))
    try:
        rows_info = connection.execute("PRAGMA table_info(sessions)").fetchall()
        columns = [str(row[1]) for row in rows_info]
        if columns == list(expected_columns):
            return
        selected_parts = [
            column if column in columns else f"NULL AS {column}"
            for column in expected_columns
        ]
        selected = ", ".join(selected_parts)
        rows = connection.execute(f"SELECT {selected} FROM sessions").fetchall()
        connection.execute("ALTER TABLE sessions RENAME TO sessions_extra")
        table_columns = [
            "dc_id INTEGER PRIMARY KEY",
            "server_address TEXT",
            "port INTEGER",
            "auth_key BLOB",
        ]
        if "tmp_auth_key" in expected_columns:
            table_columns.append("tmp_auth_key BLOB")
        table_columns.append("takeout_id INTEGER")
        connection.execute(f"CREATE TABLE sessions ({', '.join(table_columns)})")
        placeholders = ", ".join("?" for _ in expected_columns)
        column_names = ", ".join(expected_columns)
        connection.executemany(
            f"INSERT INTO sessions ({column_names}) VALUES ({placeholders})",
            rows,
        )
        connection.execute("DROP TABLE sessions_extra")
        connection.commit()
    finally:
        connection.close()
    _sync_session_version(path, expected_columns)