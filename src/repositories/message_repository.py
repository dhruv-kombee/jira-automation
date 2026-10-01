import json
import sqlite3
from typing import Any, Dict, List, Optional
from src.database import get_db
from src.logger import logger


def store_message(normalized_message: Dict[str, Any]) -> Dict[str, Any]:
    """Store a normalized Teams message.

    Returns:
        dict: {'stored': bool, 'duplicate': bool, 'record': dict | None}
    """
    db = get_db()
    cursor = db.cursor()

    sql = """
        INSERT INTO messages (
            message_id, chat_id, team_id, channel_id,
            sender_user_id, sender_display_name,
            message_text, message_url,
            reply_to_id, attachments, reactions, ai_ticket,
            created_at, modified_at, received_at
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, datetime('now')
        )
    """

    sender = normalized_message.get("sender") or {}
    message_data = normalized_message.get("message") or {}
    attachments = normalized_message.get("attachments")
    reactions = normalized_message.get("reactions")
    ai_ticket = normalized_message.get("aiTicket") or normalized_message.get("ai_ticket")

    attachments_json = json.dumps(attachments) if attachments else None
    reactions_json = json.dumps(reactions) if reactions else None
    ai_ticket_json = json.dumps(ai_ticket) if ai_ticket else None

    params = (
        normalized_message.get("messageId"),
        normalized_message.get("chatId"),
        normalized_message.get("teamId"),
        normalized_message.get("channelId"),
        sender.get("userId"),
        sender.get("displayName"),
        message_data.get("text"),
        message_data.get("webUrl"),
        normalized_message.get("replyToId"),
        attachments_json,
        reactions_json,
        ai_ticket_json,
        message_data.get("createdAt"),
        message_data.get("modifiedAt"),
    )

    try:
        cursor.execute(sql, params)
        last_id = cursor.lastrowid
        logger.info(
            "Message stored",
            extra={
                "event": "TEAMS_MESSAGE_STORED",
                "messageId": normalized_message.get("messageId"),
                "chatId": normalized_message.get("chatId"),
                "teamId": normalized_message.get("teamId"),
                "channelId": normalized_message.get("channelId"),
                "dbId": last_id,
            },
        )
        return {"stored": True, "duplicate": False, "record": {"id": last_id}}

    except sqlite3.IntegrityError as err:
        err_msg = str(err)
        if "UNIQUE constraint failed" in err_msg or "messages.team_id" in err_msg or "messages.message_id" in err_msg:
            msg_id = normalized_message.get("messageId")
            try:
                row = cursor.execute(
                    "SELECT reactions, message_text, modified_at, jira_issue_key FROM messages WHERE message_id = ?",
                    (msg_id,),
                ).fetchone()

                prev_reactions = row["reactions"] if row else None
                prev_text = row["message_text"] if row else None
                prev_jira_key = row["jira_issue_key"] if row else None

                reactions_changed = False
                if reactions_json != prev_reactions:
                    try:
                        r1 = json.loads(reactions_json) if reactions_json else []
                        r2 = json.loads(prev_reactions) if prev_reactions else []
                        reactions_changed = (r1 != r2)
                    except Exception:
                        reactions_changed = (reactions_json != prev_reactions)

                text_changed = bool(message_data.get("text") and message_data.get("text") != prev_text)

                if reactions_changed or text_changed:
                    cursor.execute(
                        """
                        UPDATE messages
                        SET reactions = COALESCE(?, reactions),
                            message_text = COALESCE(?, message_text),
                            ai_ticket = COALESCE(?, ai_ticket),
                            modified_at = COALESCE(?, modified_at)
                        WHERE message_id = ?
                        """,
                        (reactions_json, message_data.get("text"), ai_ticket_json, message_data.get("modifiedAt"), msg_id),
                    )
                    logger.info(
                        "Message updated with latest reactions/edits",
                        extra={
                            "event": "MESSAGE_UPDATED",
                            "messageId": msg_id,
                            "reactionsChanged": reactions_changed,
                        },
                    )
                    return {
                        "stored": False,
                        "duplicate": True,
                        "updated": True,
                        "reactions_changed": reactions_changed,
                        "prev_jira_key": prev_jira_key,
                        "record": None,
                    }
                else:
                    return {
                        "stored": False,
                        "duplicate": True,
                        "updated": False,
                        "reactions_changed": False,
                        "prev_jira_key": prev_jira_key,
                        "record": None,
                    }
            except Exception as upd_err:
                logger.debug(f"Failed to check/update message: {upd_err}")
                return {"stored": False, "duplicate": True, "updated": False, "reactions_changed": False, "record": None}

        logger.error(
            "Database failure while storing message",
            extra={
                "event": "DB_ERROR",
                "error": err_msg,
                "messageId": normalized_message.get("messageId"),
            },
        )
        raise err

    except Exception as err:
        logger.error(
            "Database failure while storing message",
            extra={
                "event": "DB_ERROR",
                "error": str(err),
                "messageId": normalized_message.get("messageId"),
            },
        )
        raise err


def find_message(team_id: str, channel_id: str, message_id: str) -> Optional[Dict[str, Any]]:
    """Find a message by its Teams identifiers."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT * FROM messages
        WHERE team_id = ? AND channel_id = ? AND message_id = ?
        """,
        (team_id, channel_id, message_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_all_messages(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all stored messages, newest first."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def update_message_ai_ticket(message_id: str, ai_ticket_dict: Dict[str, Any]) -> bool:
    """Update or attach an AI-extracted ticket to an existing stored message."""
    db = get_db()
    cursor = db.cursor()
    ticket_json = json.dumps(ai_ticket_dict) if ai_ticket_dict else None
    cursor.execute(
        """
        UPDATE messages
        SET ai_ticket = ?
        WHERE message_id = ?
        """,
        (ticket_json, message_id),
    )
    return cursor.rowcount > 0
