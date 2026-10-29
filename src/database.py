import os
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from src.config import config
from src.logger import logger

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

_db_conn: Optional[Any] = None


def clean_postgres_url(raw_url: str) -> str:
    """Normalize PostgreSQL connection string, ensuring URL-encoded passwords and valid libpq parameters."""
    if not raw_url:
        return ""
    raw = raw_url.strip()
    # Handle passwords containing unencoded '@' characters
    prefix, sep, rest = raw.partition("://")
    if sep and "@" in rest:
        parts = rest.split("@")
        host_part = parts[-1]
        user_pass = "@".join(parts[:-1])
        user, p_sep, pwd = user_pass.partition(":")
        if p_sep and pwd:
            pwd_encoded = urllib.parse.quote(urllib.parse.unquote(pwd))
            raw = f"{prefix}://{user}:{pwd_encoded}@{host_part}"

    parsed = urllib.parse.urlparse(raw)
    query_params = urllib.parse.parse_qs(parsed.query)
    # libpq does not recognize 'pgbouncer' query param
    query_params.pop("pgbouncer", None)
    query_params.setdefault("sslmode", ["require"])
    clean_query = urllib.parse.urlencode(query_params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def _translate_sql_for_postgres(sql: str, params: Any = None) -> Tuple[str, bool]:
    """Translate SQLite SQL to PostgreSQL syntax."""
    # 1. Translate datetime('now') -> CURRENT_TIMESTAMP::text
    sql = sql.replace("datetime('now')", "CURRENT_TIMESTAMP::text")

    # 2. Check if this is an INSERT statement that needs lastrowid support
    is_insert = bool(re.match(r"^\s*INSERT\s+INTO\b", sql, re.IGNORECASE))
    appended_returning = False
    if is_insert and "RETURNING" not in sql.upper():
        sql = sql.rstrip("; \t\r\n") + " RETURNING id"
        appended_returning = True

    # 3. Handle placeholders and literal % characters
    if params:
        sql = sql.replace("%", "%%").replace("?", "%s")
    else:
        sql = sql.replace("?", "%s")

    return sql, appended_returning


class PostgresCursorWrapper:
    """Cursor wrapper that translates SQLite-style queries to PostgreSQL."""

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, sql: str, params: Any = None):
        translated_sql, appended_returning = _translate_sql_for_postgres(sql, params)
        if params is not None:
            self._cur.execute(translated_sql, params)
        else:
            self._cur.execute(translated_sql)

        self.rowcount = self._cur.rowcount
        if appended_returning:
            try:
                row = self._cur.fetchone()
                self.lastrowid = row[0] if row else None
            except Exception:
                self.lastrowid = None
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def close(self):
        self._cur.close()

    def __iter__(self):
        return iter(self._cur)


class PostgresConnectionWrapper:
    """Connection wrapper providing an API compatible with sqlite3.Connection."""

    is_postgres = True

    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        return PostgresCursorWrapper(cur)

    def execute(self, sql: str, params: Any = None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        pass  # autocommit is enabled

    def rollback(self):
        try:
            self.conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def get_db_path() -> Path:
    p = Path(config.database_path).resolve()
    if "onedrive" in str(p).lower():
        safe_path = Path.home() / ".jira_automation" / "messages.db"
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not safe_path.exists():
            try:
                import shutil
                shutil.copy2(str(p), str(safe_path))
                logger.info(f"Migrated SQLite database from {p} to {safe_path} to prevent OneDrive lock stalls.")
            except Exception as e:
                logger.warning(f"Failed to copy db to safe path: {e}")
            return safe_path
    return p


def init_postgres(raw_url: str) -> PostgresConnectionWrapper:
    """Initialize PostgreSQL/Supabase database tables."""
    clean_url = clean_postgres_url(raw_url)
    logger.info("Connecting to Supabase PostgreSQL database...", extra={"event": "POSTGRES_CONNECT"})

    conn = psycopg2.connect(clean_url)
    conn.autocommit = True
    wrapper = PostgresConnectionWrapper(conn)

    global _db_conn
    _db_conn = wrapper

    wrapper.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id                  SERIAL PRIMARY KEY,
            message_id          TEXT    NOT NULL,
            chat_id             TEXT,
            team_id             TEXT,
            channel_id          TEXT,
            sender_user_id      TEXT,
            sender_display_name TEXT,
            message_text        TEXT,
            message_url         TEXT,
            reply_to_id         TEXT,
            attachments         TEXT,
            reactions           TEXT,
            ai_ticket           TEXT,
            jira_issue_key      TEXT,
            jira_issue_url      TEXT,
            created_at          TEXT,
            modified_at         TEXT,
            received_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
            confirmation_status TEXT,
            confirmation_message_id TEXT,
            reminder_sent_at    TEXT,
            reminder_count      TEXT,
            reminder_channel_status TEXT,
            reminder_email_status   TEXT,
            reminder_email_sent_at  TEXT,
            UNIQUE(message_id)
        );
    """)

    wrapper.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            id                  SERIAL PRIMARY KEY,
            user_id             TEXT,
            display_name        TEXT NOT NULL,
            email               TEXT,
            role                TEXT NOT NULL DEFAULT 'DEVELOPER',
            level               TEXT,
            specialty           TEXT,
            can_approve         INTEGER NOT NULL DEFAULT 0,
            is_active           INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
            updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
        );
    """)

    wrapper.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id                  SERIAL PRIMARY KEY,
            name                TEXT NOT NULL,
            type                TEXT NOT NULL DEFAULT 'chat',
            chat_id             TEXT,
            team_id             TEXT,
            channel_id          TEXT,
            webhook_url         TEXT,
            usage               TEXT NOT NULL DEFAULT 'CLIENT_SUPPORT',
            is_active           INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
            updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
        );
    """)

    wrapper.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id                  SERIAL PRIMARY KEY,
            category            TEXT NOT NULL,
            action              TEXT NOT NULL,
            details             TEXT NOT NULL,
            performed_by        TEXT NOT NULL DEFAULT 'Admin',
            created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
        );
    """)

    # Populate team_members directly from Member.xlsx (Single Source of Truth)
    try:
        from src.services.member_sync_service import sync_db_from_excel
        sync_db_from_excel()
    except Exception as sync_err:
        logger.warning(f"Initial sync from Member.xlsx to PostgreSQL: {sync_err}")

    # Seed default monitored channel if empty
    channel_count = wrapper.execute("SELECT COUNT(*) FROM monitored_channels").fetchone()[0]
    if channel_count == 0:
        chat_id = config.teams.chat_id or "19:c8c7d01f1cc24db4b04de10e93c865de@thread.v2"
        wrapper.execute(
            """
            INSERT INTO monitored_channels (name, type, chat_id, team_id, channel_id, webhook_url, usage, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Team To Jira Ticket Creation",
                "chat",
                chat_id,
                config.teams.team_id,
                config.teams.channel_id,
                config.teams.webhook_url,
                "CLIENT_SUPPORT",
                1,
            ),
        )

    logger.info("Supabase PostgreSQL database initialized successfully", extra={"event": "DB_INIT_POSTGRES"})
    return wrapper


def init_sqlite(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Initialize local SQLite database."""
    target_path = Path(db_path).resolve() if db_path else get_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(target_path),
        check_same_thread=False,
        isolation_level=None  # autocommit mode
    )
    conn.row_factory = sqlite3.Row

    global _db_conn
    _db_conn = conn

    # Enable WAL mode for better concurrent read/write performance
    conn.execute("PRAGMA journal_mode = WAL;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id          TEXT    NOT NULL,
            chat_id             TEXT,
            team_id             TEXT,
            channel_id          TEXT,
            sender_user_id      TEXT,
            sender_display_name TEXT,
            message_text        TEXT,
            message_url         TEXT,
            reply_to_id         TEXT,
            attachments         TEXT,
            reactions           TEXT,
            ai_ticket           TEXT,
            jira_issue_key      TEXT,
            jira_issue_url      TEXT,
            created_at          TEXT,
            modified_at         TEXT,
            received_at         TEXT    NOT NULL DEFAULT (datetime('now')),
            confirmation_status TEXT,
            confirmation_message_id TEXT,
            reminder_sent_at    TEXT,
            reminder_count      TEXT,
            reminder_channel_status TEXT,
            reminder_email_status   TEXT,
            reminder_email_sent_at  TEXT,
            UNIQUE(message_id)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT,
            display_name        TEXT NOT NULL,
            email               TEXT,
            role                TEXT NOT NULL DEFAULT 'DEVELOPER',
            level               TEXT,
            specialty           TEXT,
            can_approve         INTEGER NOT NULL DEFAULT 0,
            is_active           INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS monitored_channels (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL,
            type                TEXT NOT NULL DEFAULT 'chat',
            chat_id             TEXT,
            team_id             TEXT,
            channel_id          TEXT,
            webhook_url         TEXT,
            usage               TEXT NOT NULL DEFAULT 'CLIENT_SUPPORT',
            is_active           INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            category            TEXT NOT NULL,
            action              TEXT NOT NULL,
            details             TEXT NOT NULL,
            performed_by        TEXT NOT NULL DEFAULT 'Admin',
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Populate team_members directly from Member.xlsx (Single Source of Truth)
    try:
        from src.services.member_sync_service import sync_db_from_excel
        sync_db_from_excel()
    except Exception as sync_err:
        logger.warning(f"Initial sync from Member.xlsx: {sync_err}")

    # Seed default monitored channel if table is empty
    channel_count = conn.execute("SELECT COUNT(*) FROM monitored_channels").fetchone()[0]
    if channel_count == 0:
        chat_id = config.teams.chat_id or "19:c8c7d01f1cc24db4b04de10e93c865de@thread.v2"
        conn.execute(
            """
            INSERT INTO monitored_channels (name, type, chat_id, team_id, channel_id, webhook_url, usage, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Team To Jira Ticket Creation",
                "chat",
                chat_id,
                config.teams.team_id,
                config.teams.channel_id,
                config.teams.webhook_url,
                "CLIENT_SUPPORT",
                1,
            ),
        )

    logger.info("SQLite database initialized", extra={"event": "DB_INIT_SQLITE", "path": str(target_path)})
    return conn


def init_database(db_path: Optional[str] = None) -> Any:
    """Initialize database connection (Supabase PostgreSQL if configured, otherwise SQLite)."""
    global _db_conn

    db_url = config.database_url or os.getenv("DATABASE_URL")
    if db_url and db_url.strip() and PSYCOPG2_AVAILABLE:
        try:
            _db_conn = init_postgres(db_url.strip())
            return _db_conn
        except Exception as pg_err:
            logger.error(
                f"Failed to connect to PostgreSQL ({pg_err}). Falling back to local SQLite.",
                extra={"event": "POSTGRES_FALLBACK_ERROR"}
            )

    _db_conn = init_sqlite(db_path)
    return _db_conn


def get_db() -> Any:
    """Get the active database connection, initializing if needed."""
    global _db_conn
    if _db_conn is None:
        return init_database()
    return _db_conn


def close_database() -> None:
    """Close the database connection gracefully."""
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.close()
        except Exception:
            pass
        _db_conn = None
        logger.info("Database closed", extra={"event": "DB_CLOSE"})
