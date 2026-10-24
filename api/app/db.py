"""Доступ к PostgreSQL: миграции, снимок источника, настройки, журналы, пользователи."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.domain import Account, Service, Snapshot, Transaction
from app.scoring.engine import Transition
from app.scoring.features import FEATURES
from app.scoring.settings import ModelSettings
from app.source import SnapshotIn

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

ACCOUNT_COLS = ("id", "email", "balance", "withdrawBalance", "totalReplenished", "bonusBalance", "bonusState", "signals")
SERVICE_COLS = ("id", "name", "ownerId", "paymentTerm", "autoProlong", "createdAt", "expiresAt", "status", "price", "signals")
TX_COLS = ("id", "amount", "status", "performedAt", "createdAt", "type", "serviceId")


def create_pool(url: str) -> ConnectionPool:
    pool = ConnectionPool(
        url, min_size=1, max_size=10, open=True,
        kwargs={"row_factory": dict_row, "options": "-c TimeZone=UTC"},
    )
    pool.wait(timeout=30)
    return pool


def migrate(pool: ConnectionPool) -> list[str]:
    applied: list[str] = []
    with pool.connection() as conn:
        conn.autocommit = True
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
        conn.autocommit = False
    return applied


def _cols(cols: tuple[str, ...]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


# --- снимок источника ---------------------------------------------------------

def load_snapshot(conn: Connection) -> Snapshot:
    accounts = [Account(**r) for r in conn.execute(f"SELECT {_cols(ACCOUNT_COLS)} FROM accounts ORDER BY id")]
    services = [Service(**r) for r in conn.execute(f"SELECT {_cols(SERVICE_COLS)} FROM services ORDER BY id")]
    transactions = [Transaction(**r) for r in conn.execute(f"SELECT {_cols(TX_COLS)} FROM transactions ORDER BY id")]
    row = conn.execute("SELECT reference_at FROM source_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return Snapshot(accounts, services, transactions, row["reference_at"] if row else None)


def source_is_empty(conn: Connection) -> bool:
    return conn.execute("SELECT NOT EXISTS (SELECT 1 FROM accounts) AS empty").fetchone()["empty"]


def replace_snapshot(conn: Connection, snap: SnapshotIn, author: str, at: datetime, record: bool = True) -> dict[str, Any]:
    """Полная замена данных источника в одной транзакции + журнал смен статусов.

    record=False — откат к уже сохранённому снимку: ни журнала, ни новой записи снимка.
    """
    old = {r["id"]: (r["status"], r["ownerId"]) for r in conn.execute('SELECT id, status, "ownerId" FROM services')}
    conn.execute("TRUNCATE transactions, services, accounts")
    _insert(conn, "accounts", ACCOUNT_COLS, [a.model_dump() for a in snap.accounts])
    _insert(conn, "services", SERVICE_COLS, [s.model_dump() for s in snap.services])
    _insert(conn, "transactions", TX_COLS, [t.model_dump() for t in snap.transactions])
    for table in ("accounts", "services", "transactions"):
        conn.execute(
            f"SELECT setval(pg_get_serial_sequence('public.{table}', 'id'), GREATEST((SELECT max(id) FROM {table}), 1))"
        )

    changed = 0
    for s in snap.services:
        before = old.get(s.id)
        if record and before and before[0] != s.status:
            add_transition(conn, s.id, s.ownerId, before[0], s.status, snap.referenceAt or at, "snapshot")
            changed += 1

    counts = {"accounts": len(snap.accounts), "services": len(snap.services), "transactions": len(snap.transactions)}
    if not record:
        return {**counts, "statusChanges": 0}
    conn.execute(
        "INSERT INTO source_snapshots (data, reference_at, counts, loaded_by) VALUES (%s, %s, %s, %s)",
        (Jsonb(snap.model_dump(mode="json")), snap.referenceAt, Jsonb(counts), author),
    )
    return {**counts, "statusChanges": changed}


def _insert(conn: Connection, table: str, cols: tuple[str, ...], rows: list[dict]) -> None:
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(cols))
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {table} ({_cols(cols)}) VALUES ({placeholders})",
            [tuple(Jsonb(r[c]) if isinstance(r[c], dict) else r[c] for c in cols) for r in rows],
        )


def last_snapshot_raw(conn: Connection) -> dict | None:
    row = conn.execute("SELECT data FROM source_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return row["data"] if row else None


def snapshot_info(conn: Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, reference_at, counts, loaded_by, loaded_at FROM source_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "referenceAt": row["reference_at"],
        "counts": row["counts"],
        "loadedBy": row["loaded_by"],
        "loadedAt": row["loaded_at"],
    }


# --- журналы -----------------------------------------------------------------

def add_transition(conn: Connection, service_id: int, account_id: int, from_status: str | None,
                   to_status: str, at: datetime, source: str) -> None:
    conn.execute(
        "INSERT INTO status_transitions (service_id, account_id, from_status, to_status, at, source) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (service_id, account_id, from_status, to_status, at, source),
    )


def transitions(conn: Connection) -> list[Transition]:
    return [
        Transition(r["service_id"], r["account_id"], r["from_status"], r["to_status"], r["at"])
        for r in conn.execute("SELECT service_id, account_id, from_status, to_status, at FROM status_transitions ORDER BY at")
    ]


def log_activity(conn: Connection, kind: str, summary: str, author: str, payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO activity_log (kind, summary, payload, author) VALUES (%s, %s, %s, %s)",
        (kind, summary, Jsonb(payload or {}), author),
    )


def recent_activity(conn: Connection, limit: int) -> list[dict]:
    return list(conn.execute(
        "SELECT id, kind, summary, payload, author, created_at AS \"createdAt\" FROM activity_log ORDER BY id DESC LIMIT %s",
        (limit,),
    ))


# --- настройки модели ---------------------------------------------------------

def current_settings(conn: Connection) -> tuple[ModelSettings, int] | None:
    row = conn.execute("SELECT id, data FROM model_settings ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    data = dict(row["data"])
    # Признак удалили из кода — его сохранённый вес просто игнорируем, а не роняем сервер.
    known = {f.key for f in FEATURES}
    data["features"] = {k: v for k, v in data.get("features", {}).items() if k in known}
    return ModelSettings.model_validate(data), row["id"]


def save_settings(conn: Connection, settings: ModelSettings, changes: list[str], author: str) -> int:
    row = conn.execute(
        "INSERT INTO model_settings (data, changes, author) VALUES (%s, %s, %s) RETURNING id",
        (Jsonb(json.loads(settings.model_dump_json())), Jsonb(changes), author),
    ).fetchone()
    return row["id"]


def settings_history(conn: Connection, limit: int) -> list[dict]:
    return list(conn.execute(
        "SELECT id AS version, changes, author, created_at AS \"createdAt\" FROM model_settings ORDER BY id DESC LIMIT %s",
        (limit,),
    ))


# --- пользователи и сессии ----------------------------------------------------

def upsert_password_user(conn: Connection, login: str, password_hash: str, display_name: str, role: str = "admin") -> None:
    conn.execute(
        "INSERT INTO app_users (login, password_hash, display_name, role) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (login) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role",
        (login, password_hash, display_name, role),
    )


USER_COLS = 'id, login, display_name AS "displayName", role, oidc_subject IS NOT NULL AS "viaSso", created_at AS "createdAt"'


def list_users(conn: Connection) -> list[dict]:
    return list(conn.execute(f"SELECT {USER_COLS} FROM app_users ORDER BY id"))


def create_user(conn: Connection, login: str, password_hash: str, display_name: str, role: str) -> dict | None:
    return conn.execute(
        f"INSERT INTO app_users (login, password_hash, display_name, role) VALUES (%s, %s, %s, %s) "
        f"ON CONFLICT (login) DO NOTHING RETURNING {USER_COLS}",
        (login, password_hash, display_name, role),
    ).fetchone()


def update_user(conn: Connection, user_id: int, fields: dict) -> dict | None:
    cols = {"displayName": "display_name", "role": "role", "passwordHash": "password_hash"}
    sets = [(cols[k], v) for k, v in fields.items() if k in cols]
    if sets:
        conn.execute(f"UPDATE app_users SET {', '.join(c + ' = %s' for c, _ in sets)} WHERE id = %s",
                     (*[v for _, v in sets], user_id))
    return conn.execute(f"SELECT {USER_COLS} FROM app_users WHERE id = %s", (user_id,)).fetchone()


def delete_user(conn: Connection, user_id: int) -> None:
    conn.execute("DELETE FROM app_users WHERE id = %s", (user_id,))


def count_admins(conn: Connection) -> int:
    return conn.execute("SELECT count(*) AS n FROM app_users WHERE role = 'admin'").fetchone()["n"]


def workload(conn: Connection, days: int) -> dict[str, dict]:
    """Работа с клиентами по сотрудникам за последние days дней: отметок, продлили, сохранено (евроценты)."""
    return {r["author"]: r for r in conn.execute(
        "SELECT author, count(*) AS actions, count(DISTINCT account_id) AS customers, "
        "count(*) FILTER (WHERE kind = 'renewed') AS renewed, "
        "coalesce(sum(at_risk) FILTER (WHERE kind = 'renewed'), 0) AS saved "
        "FROM retention_actions WHERE created_at > now() - make_interval(days => %s) GROUP BY author", (days,))}


def upsert_oidc_user(conn: Connection, subject: str, login: str, display_name: str, role: str = "marketer") -> dict:
    row = conn.execute("SELECT * FROM app_users WHERE oidc_subject = %s", (subject,)).fetchone()
    if row:
        return row
    return conn.execute(
        "INSERT INTO app_users (login, display_name, oidc_subject, role) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (login) DO UPDATE SET oidc_subject = EXCLUDED.oidc_subject RETURNING *",
        (f"sso:{login}", display_name, subject, role),
    ).fetchone()


def user_by_login(conn: Connection, login: str) -> dict | None:
    return conn.execute("SELECT * FROM app_users WHERE lower(login) = lower(%s)", (login,)).fetchone()


def create_session(conn: Connection, token_hash: str, user_id: int, expires_at: datetime) -> None:
    conn.execute("DELETE FROM app_sessions WHERE expires_at < now()")
    conn.execute(
        "INSERT INTO app_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
        (token_hash, user_id, expires_at),
    )


def session_user(conn: Connection, token_hash: str) -> dict | None:
    return conn.execute(
        "SELECT u.id, u.login, u.display_name, u.oidc_subject, u.role, s.expires_at FROM app_sessions s "
        "JOIN app_users u ON u.id = s.user_id WHERE s.token_hash = %s AND s.expires_at > now()",
        (token_hash,),
    ).fetchone()


def delete_session(conn: Connection, token_hash: str) -> None:
    conn.execute("DELETE FROM app_sessions WHERE token_hash = %s", (token_hash,))


def delete_simulated_transitions(conn: Connection) -> None:
    conn.execute("DELETE FROM status_transitions WHERE source IN ('event', 'simulator')")


# --- история score -----------------------------------------------------------

def last_scores(conn: Connection) -> dict[int, tuple[int | None, str]]:
    return {r["account_id"]: (r["score"], r["level"]) for r in conn.execute(
        "SELECT DISTINCT ON (account_id) account_id, score, level FROM score_history ORDER BY account_id, id DESC")}


def save_score_changes(conn: Connection, rows: list[tuple[int, int | None, str]], as_of: datetime, reason: str) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO score_history (account_id, score, level, as_of, reason) VALUES (%s, %s, %s, %s, %s)",
            [(a, s, lv, as_of, reason) for a, s, lv in rows],
        )


def score_history(conn: Connection, account_id: int, limit: int) -> list[dict]:
    rows = list(conn.execute(
        "SELECT score, level, as_of, reason, recorded_at FROM score_history WHERE account_id = %s ORDER BY id DESC LIMIT %s",
        (account_id, limit),
    ))
    return rows[::-1]


# --- работа с клиентом ---------------------------------------------------------

def add_action(conn: Connection, account_id: int, kind: str, reason: str | None, note: str | None, author: str,
               at_risk: int | None = None) -> dict:
    return conn.execute(
        "INSERT INTO retention_actions (account_id, kind, reason, note, author, at_risk) VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, kind, reason, note, author, created_at AS \"createdAt\"",
        (account_id, kind, reason, note, author, at_risk),
    ).fetchone()


def actions_for(conn: Connection, account_id: int, limit: int = 50) -> list[dict]:
    return list(conn.execute(
        "SELECT id, kind, reason, note, author, created_at AS \"createdAt\" FROM retention_actions "
        "WHERE account_id = %s ORDER BY id DESC LIMIT %s", (account_id, limit)))


def last_actions(conn: Connection) -> dict[int, dict]:
    return {r["account_id"]: r for r in conn.execute(
        "SELECT DISTINCT ON (account_id) account_id, kind, author, created_at FROM retention_actions ORDER BY account_id, id DESC")}


def saved_by_renewals(conn: Connection) -> int:
    """Сумма под угрозой у клиентов в момент отметки «продлил» — сохранённая выручка (евроценты)."""
    return conn.execute("SELECT coalesce(sum(at_risk), 0) AS s FROM retention_actions WHERE kind = 'renewed'").fetchone()["s"]


def action_counts(conn: Connection) -> dict[str, int]:
    return {r["kind"]: r["n"] for r in conn.execute("SELECT kind, count(*) AS n FROM retention_actions GROUP BY kind")}


def churn_reasons(conn: Connection) -> dict[str, int]:
    return {r["reason"]: r["n"] for r in conn.execute(
        "SELECT reason, count(*) AS n FROM retention_actions WHERE kind = 'churned' AND reason IS NOT NULL GROUP BY reason")}
