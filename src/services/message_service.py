import asyncio
import html
import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional
from src.config import config
from src.graph_client import (
    get_channel_message,
    get_channel_message_reply,
    get_chat_message,
    list_chat_messages,
    list_channel_messages,
    download_hosted_content,
    download_attachment_bytes,
)
from src.logger import logger
from src.repositories.message_repository import store_message
from src.services.sender_service import identify_sender_role

# In-memory cache for downloaded attachments per message_id (for Jira upload upon PM approval)
_message_attachment_cache: Dict[str, List[Dict[str, Any]]] = {}
_message_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def strip_html(html_str: str) -> str:
    """Strip HTML tags while preserving code blocks, blockquotes, and unescaping entities."""
    if not html_str:
        return ""

    text = html_str
    # Convert code blocks: <pre><code>...</code></pre> or <pre>...</pre>
    text = re.sub(
        r'<pre[^>]*><code[^>]*>(.*?)</code></pre>',
        lambda m: f"\n```\n{html.unescape(m.group(1))}\n```\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r'<pre[^>]*>(.*?)</pre>',
        lambda m: f"\n```\n{html.unescape(m.group(1))}\n```\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r'<code[^>]*>(.*?)</code>',
        lambda m: f"`{html.unescape(m.group(1))}`",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r'<blockquote[^>]*>(.*?)</blockquote>',
        lambda m: f"\n> {html.unescape(m.group(1))}\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Convert breaks, lists, and paragraph endings
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>', '• ', text, flags=re.IGNORECASE)

    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities (&nbsp;, &amp;, etc.)
    text = html.unescape(text)
    # Normalize excessive newlines and whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_inline_images(html_str: str) -> list[str]:
    """Extract hosted image content URLs from HTML message body."""
    if not html_str:
        return []
    return re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_str, flags=re.IGNORECASE)


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


def extract_linked_attachments(raw_body: str) -> List[Dict[str, Any]]:
    """Extract file attachment links embedded in HTML body or text (e.g. OneDrive, SharePoint, cloud files)."""
    if not raw_body:
        return []
    import html
    import re

    linked = []
    seen_urls = set()

    FILE_EXTENSIONS = (
        ".log", ".txt", ".csv", ".json", ".xml", ".sql", ".md",
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".tar", ".gz",
        ".py", ".js", ".ts", ".html", ".css", ".sh", ".bat"
    )

    # 1. Parse HTML <a> tags via regex: <a ... href="..." ...>anchor_text</a>
    a_pattern = re.compile(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
    for match in a_pattern.finditer(raw_body):
        href = html.unescape(match.group(1).strip())
        raw_text = re.sub(r'<[^>]+>', '', match.group(2))
        link_text = html.unescape(raw_text).strip()

        if not href or href in seen_urls:
            continue

        href_lower = href.lower()
        text_lower = link_text.lower()

        is_cloud = any(domain in href_lower for domain in ("1drv.ms", "onedrive.live.com", "sharepoint.com", "dropbox.com", "drive.google.com"))
        has_file_ext = any(ext in text_lower or ext in href_lower.split("?")[0] for ext in FILE_EXTENSIONS)

        if is_cloud or has_file_ext:
            seen_urls.add(href)
            filename = None
            if any(text_lower.endswith(ext) for ext in FILE_EXTENSIONS):
                filename = link_text
            else:
                path_part = href.split("?")[0].rstrip("/").split("/")[-1]
                if any(path_part.lower().endswith(ext) for ext in FILE_EXTENSIONS):
                    filename = path_part
                elif link_text and len(link_text) < 80 and not link_text.startswith("http"):
                    filename = link_text
                else:
                    filename = "linked_log.log" if "log" in href_lower else "shared_attachment"

            linked.append({
                "id": f"link_{abs(hash(href))}",
                "contentType": "reference",
                "name": filename,
                "contentUrl": href,
                "isLinked": True,
            })

    # 2. Match plain-text URLs in body (strip HTML tags first to avoid matching inside attributes like title="...")
    clean_plain = re.sub(r'<[^>]+>', ' ', raw_body)
    url_pattern = re.compile(r'https?://(?:1drv\.ms|onedrive\.live\.com|[a-zA-Z0-9-]+\.sharepoint\.com)/[^\s<>"]+')
    for match in url_pattern.finditer(clean_plain):
        url_str = html.unescape(match.group(0).rstrip(".,;)>]"))
        if url_str not in seen_urls and url_str.lower() not in {u.lower() for u in seen_urls}:
            seen_urls.add(url_str)
            linked.append({
                "id": f"link_{abs(hash(url_str))}",
                "contentType": "reference",
                "name": "onedrive_shared_file.log" if ".log" in url_str.lower() else "shared_document",
                "contentUrl": url_str,
                "isLinked": True,
            })

    return linked


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

    # Incorporate linked file attachments (e.g. OneDrive / SharePoint links shared in Teams chat)
    existing_urls = {att.get("contentUrl") for att in attachments if att.get("contentUrl")}
    for l_att in extract_linked_attachments(raw_body):
        if l_att["contentUrl"] not in existing_urls:
            attachments.append(l_att)
            existing_urls.add(l_att["contentUrl"])

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

    inline_images = extract_inline_images(raw_body)

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
        "inlineImages": inline_images,
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

    # Process System Event Details (e.g. member added to chat or channel)
    event_detail = graph_message.get("eventDetail") if graph_message else None
    if event_detail:
        event_type = str(event_detail.get("@odata.type") or "")
        if "membersAdded" in event_type or event_detail.get("members"):
            added_members = event_detail.get("members") or []
            for am in added_members:
                am_id = am.get("id") or am.get("userId")
                am_name = am.get("displayName") or ""
                if am_id:
                    try:
                        from src.services.member_sync_service import feed_member_to_excel
                        feed_member_to_excel(display_name=am_name, user_id=am_id)
                        logger.info(f"✨ Member added to Teams processed via eventDetail: {am_name or am_id}")
                    except Exception as ev_err:
                        logger.debug(f"EventDetail member feed error: {ev_err}")
            try:
                from src.services.member_sync_service import sync_teams_chat_roster
                sync_teams_chat_roster()
            except Exception:
                pass

    # Feed new members directly into shared Member.xlsx first (Single Source of Truth)
    sender_id = normalized["sender"].get("userId")
    sender_name = normalized["sender"].get("displayName")
    if sender_name and sender_name.strip() and sender_name.lower() != "unknown":
        try:
            from src.services.member_sync_service import feed_member_to_excel
            feed_member_to_excel(display_name=sender_name, user_id=sender_id)
        except Exception as reg_err:
            logger.debug(f"Excel member feed check: {reg_err}")

    sender_role = identify_sender_role(sender_id, sender_name)

    # Multimodal attachment downloading & caching
    downloaded_attachments: List[Dict[str, Any]] = []
    # 1. Inline hosted images (screenshots pasted directly in Teams)
    for idx, img_url in enumerate(normalized.get("inlineImages", [])):
        try:
            dl_res = await download_hosted_content(img_url)
            if dl_res:
                b_data, c_type = dl_res
                downloaded_attachments.append({
                    "name": f"screenshot_{idx+1}.png",
                    "content_type": c_type,
                    "bytes": b_data,
                })
        except Exception as dl_err:
            logger.debug(f"Could not download inline image {img_url}: {dl_err}")

    # 2. File attachments (logs, text files, images, PDFs)
    for att in normalized.get("attachments", []):
        c_url = att.get("contentUrl")
        att_name = att.get("name") or "attachment"
        c_type = att.get("contentType") or "application/octet-stream"
        if c_url:
            try:
                dl_res = await download_attachment_bytes(c_url)
                if dl_res:
                    b_data, resolved_ctype = dl_res
                    downloaded_attachments.append({
                        "name": att_name,
                        "content_type": resolved_ctype or c_type,
                        "bytes": b_data,
                    })
            except Exception as dl_err:
                logger.debug(f"Could not download attachment {att_name}: {dl_err}")

    msg_id = normalized.get("messageId")
    if msg_id and downloaded_attachments:
        _message_attachment_cache[msg_id] = downloaded_attachments

    # AI Ticket Extraction (Phase 2: Gemini Multimodal / Heuristic)
    message_text = (normalized.get("message") or {}).get("text", "")
    has_att = len(downloaded_attachments) > 0
    if any(tag in message_text.lower() for tag in ["#issue", "#bug", "#task", "#ticket", "bug", "issue"]) or has_att or sender_role == "CLIENT":
        try:
            from src.services.ai_service import extract_jira_ticket
            ai_ticket = await extract_jira_ticket(
                message_text,
                sender_name=normalized["sender"].get("displayName"),
                sender_role=sender_role,
                attachments=downloaded_attachments,
            )
            if ai_ticket.get("is_ticket_request"):
                normalized["aiTicket"] = ai_ticket
        except Exception as ai_err:
            logger.debug(f"AI ticket extraction skipped/failed: {ai_err}")

    # Pretty-print
    print_message_summary(normalized, sender_role)

    # Persist in SQLite
    result = store_message(normalized)

    # Closed-loop: Trigger PM triage & approval check when reactions changed or on update events
    change_type = (notification.get("changeType") or "").lower()
    triage_res = None
    if change_type != "created" and result.get("reactions_changed", False):
        triage_res = await check_and_auto_create_jira_ticket(normalized, sender_role)
        if triage_res:
            if triage_res.get("key"):
                normalized["jira_issue_key"] = triage_res.get("key")
                normalized["jira_issue_url"] = triage_res.get("url")
            if triage_res.get("status"):
                normalized["confirmation_status"] = triage_res.get("status")

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


async def execute_jira_ticket_creation(
    message_id: str,
    approver_name: Optional[str] = None,
    issue_idx: Optional[int] = None,
    assignee_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute Jira issue creation after PM final confirmation with strict concurrency locking."""
    async with _message_locks[message_id]:
        if not approver_name:
            from src.services.member_sync_service import get_active_pm_from_excel
            pm_info = get_active_pm_from_excel()
            approver_name = f"PM {pm_info.get('name', 'Project Manager')}"
        from src.database import get_db
        from src.services.jira_service import create_jira_issue, upload_jira_attachment
        from src.services.teams_notifier import send_ticket_created_notification, get_current_timestamp_str

        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            return {"success": False, "error": f"Message {message_id} not found in database"}

        ai_ticket = None
        if row["ai_ticket"]:
            try:
                ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
            except Exception:
                ai_ticket = None

        if not ai_ticket or not ai_ticket.get("summary"):
            from src.services.ai_service import extract_jira_ticket
            sender_role = identify_sender_role(row["sender_user_id"], row["sender_display_name"])
            cached_atts = _message_attachment_cache.get(message_id) or []
            ai_ticket = await extract_jira_ticket(
                row["message_text"] or "",
                sender_name=row["sender_display_name"],
                sender_role=sender_role,
                attachments=cached_atts,
            )

        # Determine issues to create
        issues = ai_ticket.get("issues")
        if not issues or not isinstance(issues, list):
            issues = [{
                "summary": ai_ticket.get("summary", "Teams Issue Report"),
                "description": ai_ticket.get("description", ""),
                "issue_type": ai_ticket.get("issue_type", config.jira.default_issue_type),
                "priority": ai_ticket.get("priority", "Medium"),
                "affected_module": ai_ticket.get("affected_module", "General"),
                "suggested_assignee": ai_ticket.get("suggested_assignee") or "Unassigned",
                "observed_behavior": ai_ticket.get("observed_behavior") or ai_ticket.get("evidence"),
                "evidence": ai_ticket.get("evidence") or [],
            }]

        # Normalize issue state
        for iss in issues:
            if "status" not in iss:
                iss["status"] = "APPROVED" if iss.get("jira_key") else "PENDING"

        if issue_idx is not None:
            if not (0 <= issue_idx < len(issues)):
                return {"success": False, "error": f"Issue index {issue_idx} out of range"}
            target_issue = issues[issue_idx]
            if target_issue.get("status") == "APPROVED" or target_issue.get("jira_key"):
                return {
                    "success": True,
                    "key": target_issue.get("jira_key", row["jira_issue_key"]),
                    "url": target_issue.get("jira_url", row["jira_issue_url"] or "#"),
                    "already_existed": True,
                }
            if target_issue.get("status") == "DECLINED":
                return {
                    "success": False,
                    "error": f"Cannot approve Issue #{issue_idx + 1}: This issue was already declined by PM.",
                }
            issues_to_process = [(issue_idx, target_issue)]
        else:
            # "Approve All": Only process PENDING issues; skip already approved or declined issues
            pending_issues = [
                (idx, item) for idx, item in enumerate(issues)
                if item.get("status", "PENDING") == "PENDING" and not item.get("jira_key")
            ]
            if not pending_issues:
                if row["jira_issue_key"]:
                    return {
                        "success": True,
                        "key": row["jira_issue_key"],
                        "url": row["jira_issue_url"],
                        "already_existed": True,
                    }
                return {
                    "success": False,
                    "error": "No pending issues to approve (all issues have already been declined or resolved).",
                }
            issues_to_process = pending_issues

        reporter = row["sender_display_name"] or "Client"
        created_keys = []
        created_urls = []
        created_results = []

        for idx, item in issues_to_process:
            item_summary = item.get("summary") or ai_ticket.get("summary", "Teams Issue Report")
            item_desc = item.get("description") or ai_ticket.get("description", "")
            item_type = item.get("issue_type") or ai_ticket.get("issue_type", config.jira.default_issue_type)
            item_priority = item.get("priority") or ai_ticket.get("priority", "Medium")
            item_assignee = (
                assignee_override
                or item.get("suggested_assignee")
                or ai_ticket.get("suggested_assignee")
                or "Unassigned"
            )
            item_module = item.get("affected_module") or ai_ticket.get("affected_module")
            item_evidence = item.get("evidence") or ai_ticket.get("evidence")

            ticket_data = dict(item)
            ticket_data.setdefault("reporter_name", reporter)
            ticket_data.setdefault("reporter_role", identify_sender_role(row["sender_user_id"], row["sender_display_name"]))
            ticket_data.setdefault("raw_message", row["message_text"] or "")

            res = await create_jira_issue(
                summary=item_summary,
                description=item_desc,
                issue_type=item_type,
                priority=item_priority,
                labels=ticket_data.get("labels", ["teams-automation", "pm-approved"]),
                message_id=message_id,
                assignee_name=item_assignee,
                ticket_data=ticket_data,
            )

            if res.get("success"):
                k = res.get("key")
                u = res.get("url")
                item["status"] = "APPROVED"
                item["jira_key"] = k
                item["jira_url"] = u
                item["assigned_to"] = item_assignee
                item["approved_by"] = approver_name
                item["approved_at"] = get_current_timestamp_str()
                created_keys.append(k)
                created_urls.append(u)
                created_results.append(res)

                logger.info(
                    f"🎉 Created Jira ticket {k} ({item_summary}) upon PM confirmation",
                    extra={"event": "PM_CONFIRMATION_JIRA_CREATED", "issueKey": k, "messageId": message_id},
                )

                # Upload cached attachments
                if message_id in _message_attachment_cache:
                    for att in _message_attachment_cache[message_id]:
                        try:
                            await upload_jira_attachment(
                                issue_key=k,
                                filename=att.get("name", "attachment"),
                                file_bytes=att.get("bytes", b""),
                                content_type=att.get("content_type", "application/octet-stream"),
                            )
                        except Exception as up_err:
                            logger.warning(f"Could not upload attachment to Jira {k}: {up_err}")

                # Send Jira ticket created card to Teams
                await send_ticket_created_notification(
                    ticket_key=k,
                    ticket_url=u,
                    summary=res.get("summary") or item_summary,
                    issue_type=item_type,
                    priority=item_priority,
                    assignee=item_assignee,
                    reporter=reporter,
                    approval_note=f"Approved & confirmed by {approver_name} via Teams",
                    chat_id=row["chat_id"],
                    team_id=row["team_id"],
                    channel_id=row["channel_id"],
                    parent_message_id=message_id,
                    module=item_module,
                    evidence=item_evidence,
                )

        if created_keys:
            prev_keys = [k.strip() for k in (row["jira_issue_key"] or "").split(",") if k.strip()]
            for k in created_keys:
                if k not in prev_keys:
                    prev_keys.append(k)
            primary_key = ", ".join(prev_keys)
            primary_url = created_urls[0] if created_urls else (row["jira_issue_url"] or "")

            ai_ticket["issues"] = issues
            all_approved = all(iss.get("status") == "APPROVED" for iss in issues)
            any_pending = any(iss.get("status", "PENDING") == "PENDING" for iss in issues)
            conf_status = "APPROVED" if all_approved else ("PARTIALLY_APPROVED" if any_pending else "RESOLVED")

            db.execute(
                "UPDATE messages SET confirmation_status = ?, jira_issue_key = ?, jira_issue_url = ?, ai_ticket = ? WHERE message_id = ?",
                (conf_status, primary_key, primary_url, json.dumps(ai_ticket), message_id),
            )

            try:
                from src.services.broadcaster import broadcast_message
                await broadcast_message({
                    "type": "MESSAGE_UPDATED",
                    "message": {
                        "messageId": message_id,
                        "jira_issue_key": primary_key,
                        "jira_issue_url": primary_url,
                        "confirmation_status": conf_status,
                    },
                    "stored": False,
                    "duplicate": True,
                    "updated": True,
                })
            except Exception:
                pass

            return {
                "success": True,
                "key": created_keys[0] if len(created_keys) == 1 else primary_key,
                "url": created_urls[0] if created_urls else primary_url,
                "tickets": created_results,
            }

        return {"success": False, "error": "Failed to create Jira issues"}


async def execute_jira_ticket_decline(
    message_id: str,
    approver_name: Optional[str] = None,
    reason: Optional[str] = None,
    issue_idx: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute Jira ticket decline with strict state verification and concurrency locking."""
    async with _message_locks[message_id]:
        if not approver_name:
            from src.services.member_sync_service import get_active_pm_from_excel
            pm_info = get_active_pm_from_excel()
            approver_name = f"PM {pm_info.get('name', 'Project Manager')}"
        from src.database import get_db
        from src.services.teams_notifier import send_ticket_declined_notification, get_current_timestamp_str

        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            return {"success": False, "error": f"Message {message_id} not found in database"}

        ai_ticket = None
        if row["ai_ticket"]:
            try:
                ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
            except Exception:
                ai_ticket = None

        raw_issues = (ai_ticket.get("issues") if ai_ticket else None) or ([ai_ticket] if ai_ticket else [{"summary": row["message_text"] or "Issue Report"}])
        reporter = row["sender_display_name"] or "Client"

        # Normalize issue state
        for iss in raw_issues:
            if "status" not in iss:
                iss["status"] = "APPROVED" if iss.get("jira_key") else "PENDING"

        issues_to_decline = []
        if issue_idx is not None:
            if not (0 <= issue_idx < len(raw_issues)):
                return {"success": False, "error": f"Issue index {issue_idx} out of range"}
            target = raw_issues[issue_idx]
            # CRITICAL CONCURRENCY CHECK: If already approved and in Jira, reject decline request!
            if target.get("status") == "APPROVED" or target.get("jira_key"):
                active_key = target.get("jira_key") or "Active"
                return {
                    "success": False,
                    "error": f"Cannot reject Issue #{issue_idx + 1}: Ticket {active_key} is already active in Jira! Tickets already created in Jira cannot be declined.",
                }
            if target.get("status") == "DECLINED":
                return {
                    "success": True,
                    "already_declined": True,
                    "message": f"Issue #{issue_idx + 1} was already declined.",
                }
            target["status"] = "DECLINED"
            target["declined_by"] = approver_name
            target["declined_at"] = get_current_timestamp_str()
            target["decline_reason"] = reason or "Declined by PM"
            issues_to_decline = [target]
            declined_label = f"Issue #{issue_idx + 1} ({target.get('summary', 'Issue')})"
        else:
            # "Reject All": Reject all PENDING issues. NEVER touch approved Jira tickets!
            for idx, item in enumerate(raw_issues):
                if item.get("status") != "APPROVED" and not item.get("jira_key"):
                    if item.get("status") != "DECLINED":
                        item["status"] = "DECLINED"
                        item["declined_by"] = approver_name
                        item["declined_at"] = get_current_timestamp_str()
                        item["decline_reason"] = reason or "Declined all by PM"
                        issues_to_decline.append(item)
            if not issues_to_decline:
                if any(iss.get("status") == "APPROVED" or iss.get("jira_key") for iss in raw_issues):
                    return {
                        "success": False,
                        "error": f"Cannot reject: All issues have already been approved and active in Jira ({row['jira_issue_key']})!",
                    }
                return {
                    "success": True,
                    "already_declined": True,
                    "message": "All unapproved issues have already been declined.",
                }
            declined_label = f"{len(issues_to_decline)} issue(s)"

        # Calculate overall status
        all_declined = all(iss.get("status") == "DECLINED" for iss in raw_issues)
        any_approved = any(iss.get("status") == "APPROVED" or iss.get("jira_key") for iss in raw_issues)
        any_pending = any(iss.get("status", "PENDING") == "PENDING" for iss in raw_issues)

        if all_declined:
            new_status = "DECLINED"
        elif any_approved and not any_pending:
            new_status = "RESOLVED"
        elif any_approved:
            new_status = "PARTIALLY_APPROVED"
        else:
            new_status = "PARTIALLY_DECLINED"

        if ai_ticket:
            ai_ticket["issues"] = raw_issues

        db.execute(
            "UPDATE messages SET confirmation_status = ?, ai_ticket = ? WHERE message_id = ?",
            (new_status, json.dumps(ai_ticket) if ai_ticket else None, message_id),
        )

        logger.info(
            f"❌ Ticket creation declined for {declined_label} by {approver_name} for message {message_id}",
            extra={"event": "PM_DISAPPROVAL_TICKET_DECLINED", "messageId": message_id, "approver": approver_name, "issueIdx": issue_idx},
        )

        # Post decline notification to Teams
        notify_res = await send_ticket_declined_notification(
            message_id=message_id,
            issues=issues_to_decline,
            reporter=reporter,
            approver=approver_name,
            reason=reason or (f"Rejected by PM: {declined_label}" if issue_idx is not None else None),
            chat_id=row["chat_id"],
            team_id=row["team_id"],
            channel_id=row["channel_id"],
        )

        try:
            from src.services.broadcaster import broadcast_message
            await broadcast_message({
                "type": "MESSAGE_UPDATED",
                "message": {
                    "messageId": message_id,
                    "confirmation_status": new_status,
                },
                "stored": False,
                "duplicate": True,
                "updated": True,
            })
        except Exception:
            pass

        return {
            "success": True,
            "status": new_status,
            "approver": approver_name,
            "declined_label": declined_label,
            "notification": notify_res,
        }


async def check_and_auto_create_jira_ticket(
    normalized_message: Dict[str, Any], sender_role: str
) -> Optional[Dict[str, Any]]:
    """Two-step PM triage & approval state machine:

    Step 1: When PM reacts with 🎟️ / 🎫 on a client issue message:
            Extracts issue(s), saves draft, sets confirmation_status='AWAITING_FINAL_CONFIRMATION',
            and sends the Issue Triage Confirmation Card to Teams (with Approve / Decline options).

    Step 2: When message is AWAITING_FINAL_CONFIRMATION:
            - If PM reacts with 🎟️ / 🎫: Creates ticket(s) in Jira and posts confirmation card.
            - If PM reacts with ❌ / 👎: Cancels creation, sets status='DECLINED', and posts declined card.
    """
    from src.services.sender_service import (
        is_pm_approval,
        is_pm_disapproval,
        is_pm_confirmation_approval,
        is_ticket_approval_reaction,
        is_ticket_disapproval_reaction,
    )
    from src.database import get_db

    reactions = normalized_message.get("reactions") or []
    has_approval = is_pm_approval(reactions)
    has_conf_approval = is_pm_confirmation_approval(reactions)
    has_disapproval = is_pm_disapproval(reactions)

    if not has_approval and not has_conf_approval and not has_disapproval:
        return None

    if not config.jira.is_configured:
        logger.debug("PM reaction detected, but Jira is not configured in .env")
        return None

    msg_id = normalized_message.get("messageId")
    if not msg_id:
        return None

    db = get_db()
    row = db.execute(
        "SELECT * FROM messages WHERE message_id = ?",
        (msg_id,),
    ).fetchone()

    if not row:
        return None

    if row["jira_issue_key"]:
        # Already created in Jira
        return {"key": row["jira_issue_key"], "url": row["jira_issue_url"], "already_existed": True}

    confirmation_status = row["confirmation_status"] or ""

    # Determine approver name from reactions dynamically using Member.xlsx
    from src.services.member_sync_service import get_active_pm_from_excel, get_member_by_id_or_name
    active_pm = get_active_pm_from_excel()
    approver_name = f"PM {active_pm.get('name', 'Project Manager')}"
    for r in reactions:
        d_name = r.get("displayName") or ""
        u_id = r.get("userId") or ""
        m = get_member_by_id_or_name(user_id=u_id, display_name=d_name)
        if m:
            m_role = (m.get("role") or "").upper()
            if m_role == "PM":
                approver_name = f"PM {m.get('display_name')}"
                break
            elif m.get("can_approve"):
                approver_name = f"{m.get('display_name')} (Authorized Approver)"
                break
            elif config.roles.allow_self_approval and m_role == "CLIENT":
                approver_name = f"{m.get('display_name')} (Client Approver)"
                break

    # Check if this message is a client text message
    is_client_message = bool(row and row["message_text"])

    # If this is a client issue message that has not yet been triaged:
    # A reaction with ticket emoji (🎟️, 🎫) or thumbs up must execute STEP 1
    # to display the Confirmation Block in Teams for PM approval.
    target_msg_id = msg_id
    if is_client_message and confirmation_status != "AWAITING_FINAL_CONFIRMATION":
        if not (has_approval or has_conf_approval):
            return None
        # Fall through to STEP 1 below to post the Confirmation Block!

    elif confirmation_status != "AWAITING_FINAL_CONFIRMATION" and (has_conf_approval or has_disapproval):
        # This is a reaction on an Adaptive Card or bot message in Teams (not a text message).
        # Map it to the pending message awaiting final confirmation in this chat.
        chat_id = normalized_message.get("chatId") or (row["chat_id"] if row else None)
        team_id = normalized_message.get("teamId") or (row["team_id"] if row else None)
        channel_id = normalized_message.get("channelId") or (row["channel_id"] if row else None)

        query = """
            SELECT * FROM messages
            WHERE confirmation_status = 'AWAITING_FINAL_CONFIRMATION'
              AND (jira_issue_key IS NULL OR jira_issue_key = '')
        """
        params = []
        if chat_id:
            query += " AND chat_id = ?"
            params.append(chat_id)
        elif team_id and channel_id:
            query += " AND team_id = ? AND channel_id = ?"
            params.extend([team_id, channel_id])

        query += " ORDER BY id DESC LIMIT 1"
        pending_row = db.execute(query, tuple(params)).fetchone()
        if pending_row:
            logger.info(
                f"Mapped card reaction on {msg_id} to pending message {pending_row['message_id']}",
                extra={"event": "CARD_REACTION_MAPPED", "cardMessageId": msg_id, "targetMessageId": pending_row["message_id"]},
            )
            row = pending_row
            target_msg_id = pending_row["message_id"]
            confirmation_status = pending_row["confirmation_status"] or ""

    # =========================================================================
    # STEP 2: Final confirmation resolution (if already awaiting confirmation)
    # =========================================================================
    if confirmation_status == "AWAITING_FINAL_CONFIRMATION":
        if has_disapproval:
            return await execute_jira_ticket_decline(target_msg_id, approver_name=approver_name)
        elif has_conf_approval:
            return await execute_jira_ticket_creation(target_msg_id, approver_name=approver_name)
        return None

    # If already declined, do not process unless re-approved
    if confirmation_status == "DECLINED" and not has_approval:
        return None

    # =========================================================================
    # STEP 1: Issue triage & confirmation request (upon first PM 🎟️/🎫 reaction)
    # =========================================================================
    if not (has_approval or has_conf_approval):
        # Reaction was disapproval on a non-triaged message; ignore
        return None

    # Retrieve or extract AI ticket draft
    ai_ticket = normalized_message.get("aiTicket")
    if not ai_ticket and row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            ai_ticket = None

    raw_text = (normalized_message.get("message") or {}).get("text") or row["message_text"] or ""
    reporter_name = normalized_message.get("sender", {}).get("displayName") or row["sender_display_name"] or "Client"

    # Context enrichment: If message is a reply in a thread, pull parent message context
    reply_to_id = normalized_message.get("replyToId") or row["reply_to_id"]
    if reply_to_id:
        from src.repositories.message_repository import get_parent_message
        parent_msg = get_parent_message(reply_to_id)
        if parent_msg and parent_msg.get("message_text"):
            raw_text = f"[In reply to thread: \"{parent_msg.get('message_text')}\"]\n\n{raw_text}"
            if msg_id not in _message_attachment_cache and reply_to_id in _message_attachment_cache:
                _message_attachment_cache[msg_id] = _message_attachment_cache[reply_to_id]

    if not ai_ticket or not ai_ticket.get("summary"):
        from src.services.ai_service import extract_jira_ticket
        cached_atts = _message_attachment_cache.get(msg_id) or []
        ai_ticket = await extract_jira_ticket(
            raw_text,
            sender_name=reporter_name,
            sender_role=sender_role,
            attachments=cached_atts,
        )

    # Only send confirmation card if AI identifies an actual issue/bug
    if not ai_ticket.get("is_ticket_request"):
        logger.info(
            "PM approved message, but content is not an issue/ticket request",
            extra={"event": "PM_APPROVAL_NON_TICKET", "messageId": msg_id},
        )
        return None

    # Ensure issues list is populated
    issues = ai_ticket.get("issues")
    if not issues or not isinstance(issues, list):
        issues = [{
            "summary": ai_ticket.get("summary", "Issue Report"),
            "description": ai_ticket.get("description", ""),
            "issue_type": ai_ticket.get("issue_type", "Bug"),
            "priority": ai_ticket.get("priority", "Medium"),
            "affected_module": ai_ticket.get("affected_module", "General"),
            "suggested_assignee": ai_ticket.get("suggested_assignee") or "Unassigned",
            "observed_behavior": ai_ticket.get("observed_behavior") or ai_ticket.get("evidence"),
            "evidence": ai_ticket.get("evidence") or [],
        }]
        ai_ticket["issues"] = issues

    if not ai_ticket.get("reporter_name"):
        ai_ticket["reporter_name"] = reporter_name
    if not ai_ticket.get("reporter_role"):
        ai_ticket["reporter_role"] = sender_role
    if not ai_ticket.get("raw_message"):
        ai_ticket["raw_message"] = raw_text

    # Update SQLite: save ai_ticket and mark AWAITING_FINAL_CONFIRMATION
    db.execute(
        "UPDATE messages SET confirmation_status = 'AWAITING_FINAL_CONFIRMATION', ai_ticket = ? WHERE message_id = ?",
        (json.dumps(ai_ticket), msg_id),
    )

    # Check for potential recent duplicates
    from src.repositories.message_repository import find_recent_similar_tickets
    dup_candidates = find_recent_similar_tickets(
        summary=ai_ticket.get("summary", ""),
        chat_id=normalized_message.get("chatId") or row["chat_id"],
    )
    duplicate_warning = None
    if dup_candidates:
        other_candidates = [d for d in dup_candidates if d.get("message_id") != msg_id]
        if other_candidates:
            duplicate_warning = other_candidates[0]

    extractor_mode = ai_ticket.get("extractor")

    # Post Pending Approval Card to Teams
    from src.services.teams_notifier import send_pending_approval_notification
    notify_res = await send_pending_approval_notification(
        message_id=msg_id,
        issues=issues,
        reporter=reporter_name,
        raw_message=raw_text,
        chat_id=normalized_message.get("chatId") or row["chat_id"],
        team_id=normalized_message.get("teamId") or row["team_id"],
        channel_id=normalized_message.get("channelId") or row["channel_id"],
        parent_message_id=msg_id,
        duplicate_warning=duplicate_warning,
        extractor_mode=extractor_mode,
    )

    logger.info(
        f"📋 Sent issue triage confirmation card to Teams for message {msg_id} ({len(issues)} issue(s))",
        extra={"event": "PM_PENDING_CONFIRMATION_SENT", "messageId": msg_id, "issuesCount": len(issues)},
    )

    # Broadcast updated message state to dashboard
    try:
        from src.services.broadcaster import broadcast_message
        normalized_message["confirmation_status"] = "AWAITING_FINAL_CONFIRMATION"
        normalized_message["aiTicket"] = ai_ticket
        await broadcast_message({
            "type": "MESSAGE_UPDATED",
            "message": normalized_message,
            "senderRole": sender_role,
            "stored": False,
            "duplicate": True,
            "updated": True,
        })
    except Exception:
        pass

    return {
        "status": "AWAITING_FINAL_CONFIRMATION",
        "issues_count": len(issues),
        "notification": notify_res,
    }


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
        # During background backfill sync, use fast rule-based extraction so we never exceed Gemini 15 RPM limits
        msg_text = (normalized.get("message") or {}).get("text", "")
        if not existing_ai and any(tag in msg_text.lower() for tag in ["#issue", "#bug", "#task", "#ticket", "bug", "issue"]):
            try:
                from src.services.ai_service import _rule_based_fallback
                ai_ticket = _rule_based_fallback(
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
