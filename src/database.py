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

    # Safe migration for existing databases
    for col in ["reactions", "ai_ticket", "jira_issue_key", "jira_issue_url"]:
        try:
            _db_conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT;")
        except Exception:
            pass

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
