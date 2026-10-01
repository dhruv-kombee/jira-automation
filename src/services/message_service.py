import html
import re
from typing import Any, Dict, Optional
from src.config import config
from src.graph_client import (
    get_channel_message,
    get_channel_message_reply,
    get_chat_message,
    list_chat_messages,
    list_channel_messages,
)
from src.logger import logger
from src.repositories.message_repository import store_message
from src.services.sender_service import identify_sender_role


def strip_html(html_str: str) -> str:
    """Strip HTML tags and unescape entities from message body."""
    if not html_str:
        return ""

    # Replace <br> and </p> tags with newlines
    text = re.sub(r'<br\s*/?>', '\n', html_str, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities (&nbsp;, &amp;, etc.)
    text = html.unescape(text)
    # Normalize excessive newlines and whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_resource_path(resource: str) -> Optional[Dict[str, Optional[str]]]:
    """Parse a Microsoft Graph resource path to extract Teams identifiers.

    Handles group chats, top-level channel messages, and thread replies:
      /chats('...')/messages('...')
      /teams('...')/channels('...')/messages('...')
      /teams('...')/channels('...')/messages('...')/replies('...')
    """
    if not resource:
        return None

    # Match chat message pattern: /chats('...')/messages('...')
    chat_pattern = re.compile(
        r"chats\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = chat_pattern.search(resource)
    if match:
        return {
            "type": "chat",
            "chat_id": match.group(1),
            "team_id": None,
            "channel_id": None,
            "message_id": match.group(2),
            "parent_message_id": None,
            "reply_message_id": None,
        }

    # Match reply pattern first (more specific)
    reply_pattern = re.compile(
        r"teams\(?'?([^'/)]+)'?\)?/channels\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?/replies\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = reply_pattern.search(resource)
    if match:
        return {
            "type": "channel",
            "chat_id": None,
            "team_id": match.group(1),
            "channel_id": match.group(2),
            "message_id": match.group(4),  # The reply ID is the actual message
            "parent_message_id": match.group(3),
            "reply_message_id": match.group(4),
        }

    # Match top-level channel message pattern
    message_pattern = re.compile(
        r"teams\(?'?([^'/)]+)'?\)?/channels\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = message_pattern.search(resource)
    if match:
        return {
            "type": "channel",
            "chat_id": None,
            "team_id": match.group(1),
            "channel_id": match.group(2),
            "message_id": match.group(3),
            "parent_message_id": None,
            "reply_message_id": None,
        }

    return None


def normalize_message(
    graph_msg: Dict[str, Any],
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize a Microsoft Graph chatMessage resource into our internal schema."""
    sender_info = (graph_msg.get("from") or {}).get("user") or {}

    raw_body = (graph_msg.get("body") or {}).get("content") or ""
    content_type = (graph_msg.get("body") or {}).get("contentType", "html").lower()

    if content_type == "text":
        text_content = raw_body
    else:
        text_content = strip_html(raw_body)

    attachments_raw = graph_msg.get("attachments") or []
    attachments = [
        {
            "id": att.get("id"),
            "contentType": att.get("contentType"),
            "name": att.get("name"),
            "contentUrl": att.get("contentUrl"),
        }
        for att in attachments_raw
    ]

    reactions_raw = graph_msg.get("reactions") or []
    reactions = [
        {
            "reactionType": r.get("reactionType"),
            "displayName": r.get("displayName") or "Like",
            "userId": (r.get("user") or {}).get("user", {}).get("id"),
            "createdDateTime": r.get("createdDateTime"),
        }
        for r in reactions_raw
    ]

    return {
        "messageId": graph_msg.get("id"),
        "chatId": chat_id,
        "teamId": team_id,
        "channelId": channel_id,
        "sender": {
            "userId": sender_info.get("id"),
            "displayName": sender_info.get("displayName"),
        },
        "message": {
            "text": text_content.strip(),
            "createdAt": graph_msg.get("createdDateTime"),
            "modifiedAt": graph_msg.get("lastModifiedDateTime"),
            "webUrl": graph_msg.get("webUrl"),
        },
        "replyToId": graph_msg.get("replyToId"),
        "attachments": attachments,
        "reactions": reactions,
    }


def print_message_summary(normalized: Dict[str, Any], sender_role: str) -> None:
    """Print a formatted summary of the received message to console/logs."""
    divider = "═" * 50
    sender = normalized.get("sender") or {}
    msg = normalized.get("message") or {}
    attachments = normalized.get("attachments") or []

    target_info = (
        f"  Chat ID:      {normalized.get('chatId')}"
        if normalized.get("chatId")
        else f"  Team ID:      {normalized.get('teamId')}\n  Channel ID:   {normalized.get('channelId')}"
    )

    summary = f"""
{divider}
  Teams message received
{divider}

  Message ID:   {normalized.get('messageId')}
{target_info}

  Sender:
    User ID:      {sender.get('userId')}
    Display Name: {sender.get('displayName')}
    Role:         {sender_role}

  Message:
    {msg.get('text')}

  Created:      {msg.get('createdAt')}
  Modified:     {msg.get('modifiedAt') or 'N/A'}
  Web URL:      {msg.get('webUrl') or 'N/A'}

  Reply To:     {normalized.get('replyToId') or 'N/A'}
  Attachments:  {len(attachments)}
{divider}
"""
    logger.info(summary)


async def process_teams_message(notification: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single Graph change notification for a Teams message (chat or channel)."""
    resource = notification.get("resource")
    ids = parse_resource_path(resource)

    if not ids:
        logger.error(
            "Failed to parse Graph resource path",
            extra={"event": "GRAPH_API_ERROR", "resource": resource},
        )
        raise ValueError(f"Cannot parse resource path: {resource}")

    msg_type = ids.get("type")
    message_id = ids["message_id"]

    if msg_type == "chat":
        chat_id = ids["chat_id"]
        if config.teams.chat_id and chat_id != config.teams.chat_id:
            logger.warning(
                "Notification for unexpected chat",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "expected": config.teams.chat_id,
                    "received": chat_id,
                },
            )

        try:
            graph_message = await get_chat_message(chat_id, message_id)
        except Exception as err:
            logger.error(
                "Failed to retrieve Teams chat message",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "chatId": chat_id,
                    "messageId": message_id,
                    "error": str(err),
                },
            )
            raise err

        normalized = normalize_message(graph_message, chat_id=chat_id)

    else:
        team_id = ids["team_id"]
        channel_id = ids["channel_id"]
        parent_message_id = ids["parent_message_id"]
        reply_message_id = ids["reply_message_id"]

        if config.teams.team_id and team_id != config.teams.team_id:
            logger.warning(
                "Notification for unexpected team",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "expected": config.teams.team_id,
                    "received": team_id,
                },
            )

        if config.teams.channel_id and channel_id != config.teams.channel_id:
            logger.warning(
                "Notification for unexpected channel",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "expected": config.teams.channel_id,
                    "received": channel_id,
                },
            )

        try:
            if reply_message_id:
                graph_message = await get_channel_message_reply(
                    team_id, channel_id, parent_message_id, reply_message_id
                )
            else:
                graph_message = await get_channel_message(team_id, channel_id, message_id)
        except Exception as err:
            logger.error(
                "Failed to retrieve Teams channel message",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "messageId": reply_message_id or message_id,
                    "error": str(err),
                },
            )
            raise err

        normalized = normalize_message(graph_message, team_id=team_id, channel_id=channel_id)

    # Identify sender role
    sender_role = identify_sender_role(
        normalized["sender"].get("userId"),
        normalized["sender"].get("displayName"),
    )

    # AI Ticket Extraction (Phase 2: Gemini / Heuristic)
    message_text = (normalized.get("message") or {}).get("text", "")
    if any(tag in message_text.lower() for tag in ["#issue", "#bug", "#task", "#ticket", "bug", "issue"]) or sender_role == "CLIENT":
        try:
            from src.services.ai_service import extract_jira_ticket
            ai_ticket = await extract_jira_ticket(
                message_text,
                sender_name=normalized["sender"].get("displayName"),
                sender_role=sender_role,
            )
            if ai_ticket.get("is_ticket_request"):
                normalized["aiTicket"] = ai_ticket
        except Exception as ai_err:
            logger.debug(f"AI ticket extraction skipped/failed: {ai_err}")

    # Pretty-print
    print_message_summary(normalized, sender_role)

    # Persist in SQLite
    result = store_message(normalized)

    is_new = bool(result.get("stored")) and not bool(result.get("duplicate"))
    event_type = "NEW_MESSAGE" if is_new else "MESSAGE_UPDATED"

    # Broadcast to real-time dashboard
    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": event_type,
            "message": normalized,
            "senderRole": sender_role,
            "stored": result.get("stored", False),
            "duplicate": result.get("duplicate", False),
            "updated": result.get("updated", False),
        })
    except Exception as b_err:
        logger.debug(f"Broadcast notice error: {b_err}")

    return {"normalized": normalized, "sender_role": sender_role, "result": result}


async def sync_recent_messages(top: int = 15) -> Dict[str, Any]:
    """Fetch recent messages directly from Microsoft Graph to backfill any missed events."""
    chat_id = config.teams.chat_id
    team_id = config.teams.team_id
    channel_id = config.teams.channel_id

    raw_messages = []
    try:
        if chat_id:
            raw_messages = await list_chat_messages(chat_id, top=top)
        elif team_id and channel_id:
            raw_messages = await list_channel_messages(team_id, channel_id, top=top)
    except Exception as err:
        logger.warning(f"Failed to fetch recent messages for sync: {err}")
        return {"synced": 0, "new": 0, "updated": 0, "error": str(err)}

    new_count = 0
    updated_count = 0

    # Reverse so we process oldest first up to newest
    for graph_msg in reversed(raw_messages):
        # Ignore system event messages without user sender
        from_user = (graph_msg.get("from") or {}).get("user")
        if not from_user or not graph_msg.get("id"):
            continue

        normalized = normalize_message(
            graph_msg,
            team_id=team_id,
            channel_id=channel_id,
            chat_id=chat_id,
        )

        sender_role = identify_sender_role(
            normalized["sender"].get("userId"),
            normalized["sender"].get("displayName"),
        )

        # AI Ticket Extraction for issue/bug requests
        msg_text = (normalized.get("message") or {}).get("text", "")
        if any(tag in msg_text.lower() for tag in ["#issue", "#bug", "#task", "#ticket", "bug", "issue"]):
            try:
                from src.services.ai_service import extract_jira_ticket
                ai_ticket = await extract_jira_ticket(
                    msg_text,
                    sender_name=normalized["sender"].get("displayName"),
                    sender_role=sender_role,
                )
                if ai_ticket.get("is_ticket_request"):
                    normalized["aiTicket"] = ai_ticket
            except Exception:
                pass

        result = store_message(normalized)
        if result.get("stored"):
            new_count += 1
            # Broadcast new message to dashboard
            try:
                from src.services.broadcaster import broadcast_message
                await broadcast_message({
                    "type": "NEW_MESSAGE",
                    "message": normalized,
                    "senderRole": sender_role,
                    "stored": True,
                    "duplicate": False,
                })
            except Exception:
                pass
        elif result.get("updated"):
            updated_count += 1
            # Broadcast updated reaction/edit
            try:
                from src.services.broadcaster import broadcast_message
                await broadcast_message({
                    "type": "MESSAGE_UPDATED",
                    "message": normalized,
                    "senderRole": sender_role,
                    "stored": False,
                    "duplicate": True,
                    "updated": True,
                })
            except Exception:
                pass

    return {
        "synced": len(raw_messages),
        "new": new_count,
        "updated": updated_count,
    }
