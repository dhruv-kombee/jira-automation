import sqlite3
import os
from pathlib import Path
from typing import Optional
from src.config import config
from src.logger import logger

_db_conn: Optional[sqlite3.Connection] = None


def get_db_path() -> Path:
    return Path(config.database_path).resolve()


def init_database(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Initialize the SQLite database and create the messages table.

    The schema is designed to be easily replaced by PostgreSQL or another
    production database in a future phase.
    """
    global _db_conn

    target_path = Path(db_path).resolve() if db_path else get_db_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    _db_conn = sqlite3.connect(
        str(target_path),
        check_same_thread=False,
        isolation_level=None  # autocommit mode
    )
    _db_conn.row_factory = sqlite3.Row

    # Enable WAL mode for better concurrent read/write performance
    _db_conn.execute("PRAGMA journal_mode = WAL;")

    _db_conn.execute("""
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

            UNIQUE(message_id)
        );
    """)

    _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT,
            display_name        TEXT NOT NULL,
            email               TEXT,
            role                TEXT NOT NULL DEFAULT 'DEVELOPER',
            specialty           TEXT,
            can_approve         INTEGER NOT NULL DEFAULT 0,
            is_active           INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    _db_conn.execute("""
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

    _db_conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            category            TEXT NOT NULL,
            action              TEXT NOT NULL,
            details             TEXT NOT NULL,
            performed_by        TEXT NOT NULL DEFAULT 'Admin',
            created_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Safe migration for existing databases
    for col in ["reactions", "ai_ticket", "jira_issue_key", "jira_issue_url", "confirmation_status", "confirmation_message_id"]:
        try:
            _db_conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT;")
        except Exception:
            pass

    # Seed default team members if table is empty
    member_count = _db_conn.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]
    if member_count == 0:
        default_members = [
            (
                config.roles.client or "35e03956-1723-469c-b561-90f03fc566ed",
                "Dhruv dobariya",
                "dhruv.d.kombee@gmail.com",
                "CLIENT",
                "Client Product Owner",
                1 if getattr(config.roles, "allow_self_approval", True) else 0,
                1,
            ),
            (
                config.roles.pm or "d7bc3c28-33d9-4973-816e-445d51556b8b",
                "Santosh Yadav",
                "santosh.yadav@kombee.com",
                "PM",
                "Project Manager / Scrum Master",
                1,
                1,
            ),
            (
                config.roles.developer or "c5a63f53-cc7a-4c05-ac9a-77e6991bc974",
                "Musaib Khan",
                "musaib.khan@kombee.com",
                "DEVELOPER",
                "Frontend & UI Lead",
                0,
                1,
            ),
            (
                "",
                "Hemil Ghori",
                "hemil.ghori@kombee.com",
                "DEVELOPER",
                "Backend & API Lead",
                0,
                1,
            ),
        ]
        _db_conn.executemany(
            """
            INSERT INTO team_members (user_id, display_name, email, role, specialty, can_approve, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            default_members,
        )

    # Seed default monitored channel if table is empty
    channel_count = _db_conn.execute("SELECT COUNT(*) FROM monitored_channels").fetchone()[0]
    if channel_count == 0:
        chat_id = config.teams.chat_id or "19:c8c7d01f1cc24db4b04de10e93c865de@thread.v2"
        _db_conn.execute(
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

    logger.info("Database initialized", extra={"event": "DB_INIT", "path": str(target_path)})
    return _db_conn


def get_db() -> sqlite3.Connection:
    """Get the active database connection, initializing if needed."""
    global _db_conn
    if _db_conn is None:
        return init_database()
    return _db_conn


def close_database() -> None:
    """Close the database connection gracefully."""
    global _db_conn
    if _db_conn is not None:
        _db_conn.close()
        _db_conn = None
        logger.info("Database closed", extra={"event": "DB_CLOSE"})
