from __future__ import annotations

from dataclasses import dataclass, fields
import sqlite3
from typing import Any

from tglol.config import Config
from tglol.registration import services_to_storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT,
    telegram_user_id INTEGER,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    session_path TEXT NOT NULL,
    json_original_path TEXT,
    json_effective_path TEXT,
    json_source TEXT NOT NULL,
    twofa_password TEXT,
    source_type TEXT NOT NULL,
    account_stage TEXT NOT NULL DEFAULT 'nereg',
    registration_service TEXT,
    registration_services TEXT,
    status TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    issued_since_reset INTEGER NOT NULL DEFAULT 0,
    reset_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS code_receiver_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    phone TEXT,
    telegram_user_id INTEGER,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    session_path TEXT NOT NULL,
    json_original_path TEXT,
    json_effective_path TEXT,
    json_source TEXT NOT NULL,
    twofa_password TEXT,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

"""


@dataclass(frozen=True)
class Account:
    id: int
    phone: str | None
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    session_path: str
    json_original_path: str | None
    json_effective_path: str | None
    json_source: str
    twofa_password: str | None
    source_type: str
    account_stage: str
    registration_service: str | None
    registration_services: str | None
    status: str
    created_by: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Worker:
    user_id: int
    first_name: str
    last_name: str | None
    username: str | None
    remaining_limit: int
    configured_limit: int
    created_by: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkerStats:
    worker: Worker
    trigger_count: int
    requested_count: int
    issued_count: int
    phones: tuple[str, ...]


@dataclass(frozen=True)
class WorkerResetResult:
    worker: Worker
    previous_remaining: int
    restored_count: int
    trigger_count: int
    requested_count: int
    issued_count: int

@dataclass(frozen=True)
class IssueStats:
    issued_since_reset: int
    reset_at: str
    updated_at: str


@dataclass(frozen=True)
class CodeReceiverAccount:
    id: int
    phone: str | None
    telegram_user_id: int | None
    username: str | None
    first_name: str | None
    last_name: str | None
    session_path: str
    json_original_path: str | None
    json_effective_path: str | None
    json_source: str
    twofa_password: str | None
    source_type: str
    status: str
    created_by: int | None
    created_at: str
    updated_at: str

def connect(config: Config) -> sqlite3.Connection:
    connection = sqlite3.connect(config.db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(config: Config) -> None:
    with connect(config) as connection:
        connection.executescript(SCHEMA)
        connection.execute("INSERT OR IGNORE INTO issue_stats (id) VALUES (1)")
        _ensure_column(connection, "accounts", "account_stage", "TEXT NOT NULL DEFAULT 'nereg'")
        _ensure_column(connection, "accounts", "registration_service", "TEXT")
        _ensure_column(connection, "accounts", "registration_services", "TEXT")
        connection.execute(
            """
            UPDATE accounts
            SET registration_services = registration_service
            WHERE registration_services IS NULL
              AND registration_service IS NOT NULL
              AND registration_service != ''
            """
        )
        connection.execute(
            """
            UPDATE accounts
            SET account_stage = 'issued', updated_at = datetime('now')
            WHERE account_stage = 'processing'
            """
        )



def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _unique_legacy_table_name(connection: sqlite3.Connection, base: str) -> str:
    if not _table_exists(connection, base):
        return base
    index = 1
    while _table_exists(connection, f"{base}_{index}"):
        index += 1
    return f"{base}_{index}"


def _migrate_legacy_workers_table(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "workers")
    if not columns or "user_id" in columns:
        return

    legacy_table = _unique_legacy_table_name(connection, "workers_legacy")
    connection.execute(f"ALTER TABLE workers RENAME TO {legacy_table}")
    connection.executescript(SCHEMA)

    legacy_columns = _table_columns(connection, legacy_table)
    if "telegram_id" not in legacy_columns:
        return

    name_expr = "coalesce(nullif(name, ''), 'Unknown')" if "name" in legacy_columns else "'Unknown'"
    limit_expr = "configured_limit" if "configured_limit" in legacy_columns else "0"
    created_expr = "created_at" if "created_at" in legacy_columns else "datetime('now')"
    connection.execute(
        f"""
        INSERT OR IGNORE INTO workers (
            user_id, first_name, last_name, username,
            remaining_limit, configured_limit, created_by, created_at, updated_at
        )
        SELECT telegram_id, {name_expr}, NULL, NULL,
               coalesce({limit_expr}, 0), coalesce({limit_expr}, 0),
               NULL, coalesce({created_expr}, datetime('now')), datetime('now')
        FROM {legacy_table}
        WHERE telegram_id IS NOT NULL
        """
    )

def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _account_from_row(row: sqlite3.Row) -> Account:
    values = dict(row)
    names = {field.name for field in fields(Account)}
    return Account(**{name: values.get(name) for name in names})


def _worker_from_row(row: sqlite3.Row) -> Worker:
    return Worker(**dict(row))



def _code_receiver_from_row(row: sqlite3.Row) -> CodeReceiverAccount:
    return CodeReceiverAccount(**dict(row))


def get_code_receiver(config: Config) -> CodeReceiverAccount | None:
    with connect(config) as connection:
        row = connection.execute("SELECT * FROM code_receiver_account WHERE id = 1").fetchone()
    return _code_receiver_from_row(row) if row else None


def save_code_receiver(config: Config, values: dict[str, Any]) -> None:
    payload = dict(values)
    payload["id"] = 1
    columns = (
        "id", "phone", "telegram_user_id", "username", "first_name", "last_name",
        "session_path", "json_original_path", "json_effective_path", "json_source",
        "twofa_password", "source_type", "status", "created_by", "created_at", "updated_at",
    )
    with connect(config) as connection:
        connection.execute(
            f"""
            INSERT INTO code_receiver_account ({', '.join(columns)})
            VALUES ({', '.join(':' + column for column in columns)})
            ON CONFLICT(id) DO UPDATE SET
                phone = excluded.phone,
                telegram_user_id = excluded.telegram_user_id,
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                session_path = excluded.session_path,
                json_original_path = excluded.json_original_path,
                json_effective_path = excluded.json_effective_path,
                json_source = excluded.json_source,
                twofa_password = excluded.twofa_password,
                source_type = excluded.source_type,
                status = excluded.status,
                created_by = excluded.created_by,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            {column: payload.get(column) for column in columns},
        )


def update_code_receiver_status(config: Config, status: str) -> None:
    with connect(config) as connection:
        connection.execute(
            "UPDATE code_receiver_account SET status = ?, updated_at = datetime('now') WHERE id = 1",
            (status,),
        )


def delete_code_receiver(config: Config) -> CodeReceiverAccount | None:
    with connect(config) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM code_receiver_account WHERE id = 1").fetchone()
        if row is None:
            return None
        connection.execute("DELETE FROM code_receiver_account WHERE id = 1")
    return _code_receiver_from_row(row)


def get_worker(config: Config, user_id: int) -> Worker | None:
    with connect(config) as connection:
        row = connection.execute("SELECT * FROM workers WHERE user_id = ?", (user_id,)).fetchone()
    return _worker_from_row(row) if row else None


def list_workers(config: Config) -> list[Worker]:
    with connect(config) as connection:
        rows = connection.execute(
            "SELECT * FROM workers ORDER BY first_name COLLATE NOCASE, last_name COLLATE NOCASE, user_id"
        ).fetchall()
    return [_worker_from_row(row) for row in rows]


def save_worker(
    config: Config,
    *,
    user_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
    remaining_limit: int,
    created_by: int | None,
) -> None:
    if remaining_limit < 0:
        raise ValueError("worker limit cannot be negative")
    with connect(config) as connection:
        connection.execute(
            """
            INSERT INTO workers (
                user_id, first_name, last_name, username,
                remaining_limit, configured_limit, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                remaining_limit = excluded.remaining_limit,
                configured_limit = excluded.configured_limit,
                updated_at = datetime('now')
            """,
            (user_id, first_name, last_name, username, remaining_limit, remaining_limit, created_by),
        )


def set_worker_limit(config: Config, user_id: int, remaining_limit: int) -> bool:
    if remaining_limit < 0:
        raise ValueError("worker limit cannot be negative")
    with connect(config) as connection:
        cursor = connection.execute(
            """
            UPDATE workers
            SET remaining_limit = ?, configured_limit = ?, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (remaining_limit, remaining_limit, user_id),
        )
        return bool(cursor.rowcount)


def update_worker_identity(
    config: Config,
    user_id: int,
    *,
    first_name: str,
    last_name: str | None,
    username: str | None,
) -> bool:
    with connect(config) as connection:
        cursor = connection.execute(
            """
            UPDATE workers
            SET first_name = ?, last_name = ?, username = ?, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (first_name, last_name, username, user_id),
        )
        return bool(cursor.rowcount)


def delete_worker(config: Config, user_id: int) -> bool:
    with connect(config) as connection:
        connection.execute("DELETE FROM worker_issues WHERE worker_id = ?", (user_id,))
        connection.execute("DELETE FROM worker_requests WHERE worker_id = ?", (user_id,))
        cursor = connection.execute("DELETE FROM workers WHERE user_id = ?", (user_id,))
        return bool(cursor.rowcount)


def claim_accounts_for_worker(
    config: Config,
    account_ids: list[int],
    *,
    worker_id: int | None,
    requested_count: int | None = None,
    reserve_stage: str = "issued",
) -> list[Account]:
    """Atomically log a request, reserve accounts and consume the worker's quota."""
    if requested_count is not None and requested_count < 0:
        raise ValueError("requested count cannot be negative")
    if reserve_stage not in {"processing", "issued"}:
        raise ValueError("unknown reserve stage")
    if not account_ids and worker_id is None:
        return []
    unique_ids = list(dict.fromkeys(account_ids))
    requested_count = len(unique_ids) if requested_count is None else requested_count
    with connect(config) as connection:
        connection.execute("BEGIN IMMEDIATE")
        allowed = len(unique_ids)
        if worker_id is not None:
            worker_row = connection.execute(
                "SELECT remaining_limit FROM workers WHERE user_id = ?",
                (worker_id,),
            ).fetchone()
            if not worker_row:
                return []
            allowed = min(allowed, int(worker_row["remaining_limit"]))

        rows: list[sqlite3.Row] = []
        if unique_ids and allowed > 0:
            placeholders = ",".join("?" for _ in unique_ids)
            rows = connection.execute(
                f"SELECT * FROM accounts WHERE id IN ({placeholders}) AND account_stage NOT IN ('issued', 'processing')",
                unique_ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        claimed_ids = [account_id for account_id in unique_ids if account_id in by_id][:allowed]

        request_id: int | None = None
        if worker_id is not None:
            cursor = connection.execute(
                """
                INSERT INTO worker_requests (worker_id, requested_count, issued_count)
                VALUES (?, ?, ?)
                """,
                (worker_id, requested_count, len(claimed_ids)),
            )
            request_id = int(cursor.lastrowid)

        if not claimed_ids:
            return []

        claimed_placeholders = ",".join("?" for _ in claimed_ids)
        connection.execute(
            f"""
            UPDATE accounts
            SET account_stage = ?, registration_service = NULL,
                registration_services = NULL, updated_at = datetime('now')
            WHERE id IN ({claimed_placeholders}) AND account_stage NOT IN ('issued', 'processing')
            """,
            [reserve_stage, *claimed_ids],
        )
        if worker_id is not None:
            connection.execute(
                """
                UPDATE workers
                SET remaining_limit = remaining_limit - ?, updated_at = datetime('now')
                WHERE user_id = ?
                """,
                (len(claimed_ids), worker_id),
            )
            connection.executemany(
                """
                INSERT INTO worker_issues (request_id, worker_id, account_id, phone)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (request_id, worker_id, account_id, by_id[account_id]["phone"])
                    for account_id in claimed_ids
                ],
            )
        return [_account_from_row(by_id[account_id]) for account_id in claimed_ids]


def _worker_stats_from_connection(connection: sqlite3.Connection) -> list[WorkerStats]:
    worker_rows = connection.execute(
        "SELECT * FROM workers ORDER BY first_name COLLATE NOCASE, last_name COLLATE NOCASE, user_id"
    ).fetchall()
    request_rows = connection.execute(
        """
        SELECT worker_id,
               count(*) AS trigger_count,
               coalesce(sum(requested_count), 0) AS requested_count,
               coalesce(sum(issued_count), 0) AS issued_count
        FROM worker_requests
        GROUP BY worker_id
        """
    ).fetchall()
    totals = {
        int(row["worker_id"]): (
            int(row["trigger_count"]), int(row["requested_count"]), int(row["issued_count"])
        )
        for row in request_rows
    }
    issue_rows = connection.execute(
        "SELECT worker_id, phone FROM worker_issues ORDER BY id"
    ).fetchall()
    phones: dict[int, list[str]] = {}
    for row in issue_rows:
        phones.setdefault(int(row["worker_id"]), []).append(str(row["phone"] or "-"))

    result: list[WorkerStats] = []
    for row in worker_rows:
        worker = _worker_from_row(row)
        trigger_count, requested_count, issued_count = totals.get(worker.user_id, (0, 0, 0))
        result.append(
            WorkerStats(
                worker=worker,
                trigger_count=trigger_count,
                requested_count=requested_count,
                issued_count=issued_count,
                phones=tuple(phones.get(worker.user_id, [])),
            )
        )
    return result


def list_worker_stats(config: Config) -> list[WorkerStats]:
    with connect(config) as connection:
        return _worker_stats_from_connection(connection)


def reset_worker_limit(config: Config, user_id: int) -> WorkerResetResult | None:
    """Reset one worker's quota and statistics without affecting other workers."""
    with connect(config) as connection:
        connection.execute("BEGIN IMMEDIATE")
        item = next(
            (stats for stats in _worker_stats_from_connection(connection) if stats.worker.user_id == user_id),
            None,
        )
        if item is None:
            return None
        result = WorkerResetResult(
            worker=item.worker,
            previous_remaining=item.worker.remaining_limit,
            restored_count=max(0, item.worker.configured_limit - item.worker.remaining_limit),
            trigger_count=item.trigger_count,
            requested_count=item.requested_count,
            issued_count=item.issued_count,
        )
        connection.execute(
            """
            UPDATE workers
            SET remaining_limit = configured_limit, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (user_id,),
        )
        connection.execute("DELETE FROM worker_issues WHERE worker_id = ?", (user_id,))
        connection.execute("DELETE FROM worker_requests WHERE worker_id = ?", (user_id,))
        return result


def reset_worker_limits(config: Config) -> list[WorkerResetResult]:
    """Restore configured limits and start a fresh statistics period."""
    with connect(config) as connection:
        connection.execute("BEGIN IMMEDIATE")
        stats = _worker_stats_from_connection(connection)
        results = [
            WorkerResetResult(
                worker=item.worker,
                previous_remaining=item.worker.remaining_limit,
                restored_count=max(0, item.worker.configured_limit - item.worker.remaining_limit),
                trigger_count=item.trigger_count,
                requested_count=item.requested_count,
                issued_count=item.issued_count,
            )
            for item in stats
        ]
        connection.execute(
            """
            UPDATE workers
            SET remaining_limit = configured_limit, updated_at = datetime('now')
            """
        )
        connection.execute("DELETE FROM worker_issues")
        connection.execute("DELETE FROM worker_requests")
        return results

def get_issue_stats(config: Config) -> IssueStats:
    with connect(config) as connection:
        connection.execute("INSERT OR IGNORE INTO issue_stats (id) VALUES (1)")
        row = connection.execute(
            "SELECT issued_since_reset, reset_at, updated_at FROM issue_stats WHERE id = 1"
        ).fetchone()
    return IssueStats(
        issued_since_reset=int(row["issued_since_reset"]),
        reset_at=str(row["reset_at"]),
        updated_at=str(row["updated_at"]),
    )


def increment_issue_stats(config: Config, count: int) -> None:
    if count <= 0:
        return
    with connect(config) as connection:
        connection.execute("INSERT OR IGNORE INTO issue_stats (id) VALUES (1)")
        connection.execute(
            """
            UPDATE issue_stats
            SET issued_since_reset = issued_since_reset + ?, updated_at = datetime('now')
            WHERE id = 1
            """,
            (count,),
        )


def reset_issue_stats(config: Config) -> IssueStats:
    with connect(config) as connection:
        connection.execute("INSERT OR IGNORE INTO issue_stats (id) VALUES (1)")
        previous = connection.execute(
            "SELECT issued_since_reset, reset_at, updated_at FROM issue_stats WHERE id = 1"
        ).fetchone()
        connection.execute(
            """
            UPDATE issue_stats
            SET issued_since_reset = 0,
                reset_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = 1
            """
        )
    return IssueStats(
        issued_since_reset=int(previous["issued_since_reset"]),
        reset_at=str(previous["reset_at"]),
        updated_at=str(previous["updated_at"]),
    )

def add_account(config: Config, values: dict[str, Any]) -> int:
    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{key}" for key in values)
    with connect(config) as connection:
        cursor = connection.execute(
            f"INSERT INTO accounts ({columns}) VALUES ({placeholders})",
            values,
        )
        return int(cursor.lastrowid)


def list_accounts(
    config: Config,
    limit: int = 20,
    offset: int = 0,
    account_stage: str | None = None,
    excluded_account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> list[Account]:
    clauses: list[str] = []
    params: list[Any] = []
    if account_stage is not None:
        clauses.append("account_stage = ?")
        params.append(account_stage)
    if excluded_account_stage is not None:
        if isinstance(excluded_account_stage, (list, tuple, set, frozenset)):
            stages = list(excluded_account_stage)
            placeholders = ",".join("?" for _ in stages)
            clauses.append(f"account_stage NOT IN ({placeholders})")
            params.extend(stages)
        else:
            clauses.append("account_stage != ?")
            params.append(excluded_account_stage)
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect(config) as connection:
        rows = connection.execute(
            f"SELECT * FROM accounts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [_account_from_row(row) for row in rows]


def list_accounts_by_scope(
    config: Config,
    *,
    account_stage: str | None = None,
    excluded_account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> list[Account]:
    return list_accounts(
        config,
        limit=100000,
        offset=0,
        account_stage=account_stage,
        excluded_account_stage=excluded_account_stage,
        registration_service=registration_service,
        excluded_registration_service=excluded_registration_service,
    )


def get_account(config: Config, account_id: int) -> Account | None:
    with connect(config) as connection:
        row = connection.execute(
            "SELECT * FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    return _account_from_row(row) if row else None


def count_accounts(config: Config) -> int:
    return count_accounts_by_stage(config)


def count_accounts_by_stage(
    config: Config,
    account_stage: str | None = None,
    excluded_account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if account_stage is not None:
        clauses.append("account_stage = ?")
        params.append(account_stage)
    if excluded_account_stage is not None:
        if isinstance(excluded_account_stage, (list, tuple, set, frozenset)):
            stages = list(excluded_account_stage)
            placeholders = ",".join("?" for _ in stages)
            clauses.append(f"account_stage NOT IN ({placeholders})")
            params.extend(stages)
        else:
            clauses.append("account_stage != ?")
            params.append(excluded_account_stage)
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with connect(config) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM accounts {where}", params).fetchone()[0])


def set_account_stage(
    config: Config,
    account_id: int,
    account_stage: str,
    registration_service: str | None = None,
    registration_services=None,
) -> None:
    if account_stage not in {"nereg", "reg", "processing", "issued"}:
        raise ValueError("unknown account stage")
    if account_stage == "reg":
        if registration_services is None and registration_service is not None:
            registration_services = (registration_service,)
        stored_services = services_to_storage(registration_services)
        if not stored_services:
            raise ValueError("unknown registration service")
        first_service = stored_services.split(",", 1)[0]
    else:
        stored_services = None
        first_service = None
    with connect(config) as connection:
        connection.execute(
            """
            UPDATE accounts
            SET account_stage = ?,
                registration_service = ?,
                registration_services = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (account_stage, first_service, stored_services, account_id),
        )


def update_account_status(config: Config, account_id: int, status: str) -> None:
    with connect(config) as connection:
        connection.execute(
            "UPDATE accounts SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, account_id),
        )


def delete_account_row(config: Config, account_id: int) -> None:
    with connect(config) as connection:
        connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def delete_accounts_by_stage(
    config: Config,
    *,
    account_stage: str | None = None,
    excluded_account_stage: str | None = None,
    registration_service: str | None = None,
    excluded_registration_service: str | None = None,
) -> int:
    clauses: list[str] = []
    params: list[Any] = []
    if account_stage is not None:
        if account_stage not in {"nereg", "reg", "processing", "issued"}:
            raise ValueError("unknown account stage")
        clauses.append("account_stage = ?")
        params.append(account_stage)
    if excluded_account_stage is not None:
        if isinstance(excluded_account_stage, (list, tuple, set, frozenset)):
            stages = list(excluded_account_stage)
            placeholders = ",".join("?" for _ in stages)
            clauses.append(f"account_stage NOT IN ({placeholders})")
            params.extend(stages)
        else:
            clauses.append("account_stage != ?")
            params.append(excluded_account_stage)
    if registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) > 0")
        params.append(f",{registration_service},")
    if excluded_registration_service is not None:
        clauses.append("instr(',' || coalesce(nullif(registration_services, ''), registration_service, '') || ',', ?) = 0")
        params.append(f",{excluded_registration_service},")
    with connect(config) as connection:
        cursor = connection.execute(
            f"DELETE FROM accounts WHERE {' AND '.join(clauses)}",
            params,
        )
        return int(cursor.rowcount or 0)
