import html
import json
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


def parse_resource_path(resource: str, resource_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Optional[str]]]:
    """Parse a Microsoft Graph resource path to extract Teams identifiers.

    Handles group chats, top-level channel messages, and thread replies:
      /chats('...')/messages('...')
      /chats/.../messages/...
      /teams('...')/channels('...')/messages('...')
      /teams('...')/channels('...')/messages('...')/replies('...')
      Also supports resource = 'chats(...)/messages' with message_id in resource_data
    """
    if not resource:
        return None

    import urllib.parse

    # 1. Match reply pattern first (more specific)
    reply_pattern = re.compile(
        r"teams\(?'?([^'/)]+)'?\)?/channels\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?/replies\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = reply_pattern.search(resource)
    if match:
        return {
            "type": "channel",
            "chat_id": None,
            "team_id": urllib.parse.unquote(match.group(1)),
            "channel_id": urllib.parse.unquote(match.group(2)),
            "message_id": urllib.parse.unquote(match.group(4)),
            "parent_message_id": urllib.parse.unquote(match.group(3)),
            "reply_message_id": urllib.parse.unquote(match.group(4)),
        }

    # 2. Match channel message with message ID in path
    channel_pattern = re.compile(
        r"teams\(?'?([^'/)]+)'?\)?/channels\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = channel_pattern.search(resource)
    if match:
        return {
            "type": "channel",
            "chat_id": None,
            "team_id": urllib.parse.unquote(match.group(1)),
            "channel_id": urllib.parse.unquote(match.group(2)),
            "message_id": urllib.parse.unquote(match.group(3)),
            "parent_message_id": None,
            "reply_message_id": None,
        }

    # 3. Match chat message with message ID in path: /chats('...')/messages('...')
    chat_pattern = re.compile(
        r"chats\(?'?([^'/)]+)'?\)?/messages\(?'?([^'/)]+)'?\)?",
        re.IGNORECASE,
    )
    match = chat_pattern.search(resource)
    if match:
        return {
            "type": "chat",
            "chat_id": urllib.parse.unquote(match.group(1)),
            "team_id": None,
            "channel_id": None,
            "message_id": urllib.parse.unquote(match.group(2)),
            "parent_message_id": None,
            "reply_message_id": None,
        }

    # 4. Fallback: match chat path /chats('...')/messages and extract message_id from resource_data
    chat_base_pattern = re.compile(
        r"chats\(?'?([^'/)]+)'?\)?/messages",
        re.IGNORECASE,
    )
    match = chat_base_pattern.search(resource)
    if match and resource_data and resource_data.get("id"):
        return {
            "type": "chat",
            "chat_id": urllib.parse.unquote(match.group(1)),
            "team_id": None,
            "channel_id": None,
            "message_id": str(resource_data["id"]),
            "parent_message_id": None,
            "reply_message_id": None,
        }

    # 5. Fallback: match channel path /teams('...')/channels('...')/messages and extract message_id from resource_data
    channel_base_pattern = re.compile(
        r"teams\(?'?([^'/)]+)'?\)?/channels\(?'?([^'/)]+)'?\)?/messages",
        re.IGNORECASE,
    )
    match = channel_base_pattern.search(resource)
    if match and resource_data and resource_data.get("id"):
        return {
            "type": "channel",
            "chat_id": None,
            "team_id": urllib.parse.unquote(match.group(1)),
            "channel_id": urllib.parse.unquote(match.group(2)),
            "message_id": str(resource_data["id"]),
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
    reactions = []
    for r in reactions_raw:
        # In Graph API, user info is located in r.user.user
        user_identity = (r.get("user") or {}).get("user") or {}
        user_name = user_identity.get("displayName") or r.get("displayName") or ""
        user_id = user_identity.get("id") or (r.get("user") or {}).get("id") or r.get("userId")
        reaction_type = r.get("reactionType") or "like"
        reactions.append({
            "reactionType": reaction_type,
            "displayName": user_name,
            "userId": user_id,
            "createdDateTime": r.get("createdDateTime"),
        })

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
    resource_data = notification.get("resourceData") or {}
    ids = parse_resource_path(resource, resource_data)

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
        import urllib.parse
        clean_expected = urllib.parse.unquote(config.teams.chat_id or "").strip().lower()
        clean_received = urllib.parse.unquote(chat_id or "").strip().lower()
        if clean_expected and clean_received != clean_expected:
            logger.warning(
                "Notification for unexpected chat",
                extra={
                    "event": "GRAPH_API_WARNING",
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

    # Closed-loop: Automatically create Jira ticket ONLY when PM (Santosh Yadav) reacted with approval
    # Strictly do NOT run on brand new "created" messages (must have PM emoji approval first),
    # and only trigger if reactions actually changed or if this was an update notification with reactions.
    change_type = (notification.get("changeType") or "").lower()
    jira_created = None
    if change_type != "created" and result.get("reactions_changed", False):
        jira_created = await check_and_auto_create_jira_ticket(normalized, sender_role)
        if jira_created:
            normalized["jira_issue_key"] = jira_created.get("key")
            normalized["jira_issue_url"] = jira_created.get("url")

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


async def check_and_auto_create_jira_ticket(
    normalized_message: Dict[str, Any], sender_role: str
) -> Optional[Dict[str, Any]]:
    """Automatically create Jira ticket when PM Santosh Yadav reacts with approval emoji."""
    from src.services.sender_service import is_pm_approval
    from src.database import get_db

    reactions = normalized_message.get("reactions") or []
    if not is_pm_approval(reactions):
        return None

    if not config.jira.is_configured:
        logger.debug("PM approval detected, but Jira is not configured in .env")
        return None

    msg_id = normalized_message.get("messageId")
    if not msg_id:
        return None

    # Check if a Jira ticket is already created for this message
    try:
        db = get_db()
        row = db.execute(
            "SELECT jira_issue_key, jira_issue_url, ai_ticket, message_text, sender_display_name FROM messages WHERE message_id = ?",
            (msg_id,),
        ).fetchone()

        if row and row["jira_issue_key"]:
            # Ticket already created, skip duplicate creation
            return {"key": row["jira_issue_key"], "url": row["jira_issue_url"], "already_existed": True}
    except Exception as db_err:
        logger.warning(f"Error checking message for existing Jira ticket: {db_err}")
        row = None

    # Retrieve or extract AI ticket draft
    ai_ticket = normalized_message.get("aiTicket")
    if not ai_ticket and row and row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            ai_ticket = None

    if not ai_ticket or not ai_ticket.get("summary"):
        from src.services.ai_service import extract_jira_ticket
        raw_text = (normalized_message.get("message") or {}).get("text") or (row["message_text"] if row else "")
        ai_ticket = await extract_jira_ticket(
            raw_text,
            sender_name=normalized_message.get("sender", {}).get("displayName") or (row["sender_display_name"] if row else None),
            sender_role=sender_role,
        )

    # Only create ticket if AI identifies an issue/task
    if not ai_ticket.get("is_ticket_request"):
        logger.info(
            "PM approved message, but content is not a ticket request",
            extra={"event": "PM_APPROVAL_NON_TICKET", "messageId": msg_id},
        )
        return None

    from src.services.jira_service import create_jira_issue
    ticket_res = await create_jira_issue(
        summary=ai_ticket.get("summary", "Teams Issue Report"),
        description=ai_ticket.get("description", ""),
        issue_type=ai_ticket.get("issue_type", config.jira.default_issue_type),
        priority=ai_ticket.get("priority", "Medium"),
        labels=ai_ticket.get("labels", ["teams-automation", "pm-approved"]),
        message_id=msg_id,
    )

    if ticket_res.get("success"):
        issue_key = ticket_res.get("key")
        issue_url = ticket_res.get("url")
        summary = ticket_res.get("summary")
        assignee = ai_ticket.get("suggested_assignee") or "Unassigned"

        logger.info(
            f"🎉 Closed-loop automation: Created Jira ticket {issue_key} upon PM approval",
            extra={"event": "PM_APPROVAL_JIRA_CREATED", "issueKey": issue_key, "messageId": msg_id},
        )

        # Post confirmation reply back to Teams chat/channel
        reply_html = (
            f"🎟️ <b>Jira Ticket Created</b>: <a href='{issue_url}'>{issue_key}</a><br/>"
            f"<b>Summary</b>: {summary}<br/>"
            f"<b>Assignee</b>: {assignee}<br/>"
            f"<i>Approved by PM Santosh Yadav via Teams 👍 reaction</i>"
        )

        chat_id = normalized_message.get("chatId")
        team_id = normalized_message.get("teamId")
        channel_id = normalized_message.get("channelId")

        if chat_id:
            from src.graph_client import send_chat_message
            await send_chat_message(chat_id, reply_html)
        elif team_id and channel_id:
            from src.graph_client import send_channel_reply
            await send_channel_reply(team_id, channel_id, msg_id, reply_html)

        return ticket_res

    return None


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

        # Check if already in DB to avoid re-extracting with Gemini
        msg_id = normalized.get("messageId")
        existing_ai = None
        if msg_id:
            try:
                row = get_db().execute("SELECT ai_ticket FROM messages WHERE message_id = ?", (msg_id,)).fetchone()
                if row and row["ai_ticket"]:
                    existing_ai = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
                    normalized["aiTicket"] = existing_ai
            except Exception:
                pass

        # AI Ticket Extraction for issue/bug requests (only if not already cached)
        msg_text = (normalized.get("message") or {}).get("text", "")
        if not existing_ai and any(tag in msg_text.lower() for tag in ["#issue", "#bug", "#task", "#ticket", "bug", "issue"]):
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

        # Do NOT auto-create Jira tickets during background sync.
        # Auto-creation is strictly reserved for live incoming PM reaction events.

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
        elif result.get("updated") and result.get("reactions_changed"):
            updated_count += 1
            # Broadcast updated reaction/edit only if reactions genuinely changed
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
