import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse

from src.config import config
from src.database import get_db
from src.logger import logger
from src.services.broadcaster import connect as ws_connect, disconnect as ws_disconnect, broadcast_message
from src.services.sender_service import Roles, identify_sender_role
from src.repositories.message_repository import store_message, get_all_messages
from src.services.subscription_manager import (
    get_active_subscription_info,
    ensure_subscription_online,
    check_tunnel_reachable,
)
from src.graph_client import renew_subscription, delete_subscription
from src.tunnel import get_active_tunnel_url

router = APIRouter()
start_timestamp = time.time()


class SimulateMessageRequest(BaseModel):
    role: str = "CLIENT"  # CLIENT, PM, DEVELOPER
    text: str
    sender_name: Optional[str] = None
    attachment_base64: Optional[str] = None  # Base64 encoded screenshot or log file
    attachment_name: Optional[str] = None    # e.g. 'error_screenshot.png'
    attachment_type: Optional[str] = None    # e.g. 'image/png'


@router.get("/api/status")
def get_system_status():
    """Return comprehensive live status for the unified dashboard."""
    tunnel_url = get_active_tunnel_url() or config.webhook_public_url
    sub_info = get_active_subscription_info()

    # Query metrics from SQLite database
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    client_id = config.roles.client or ""
    pm_id = config.roles.pm or ""
    dev_id = config.roles.developer or ""

    # Match by ID or Name
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE '%dhruv%'",
        (client_id, client_id),
    )
    client_msgs = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE '%santosh%'",
        (pm_id, pm_id),
    )
    pm_msgs = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE '%musaib%' OR LOWER(sender_display_name) LIKE '%musain%'",
        (dev_id, dev_id),
    )
    dev_msgs = cursor.fetchone()[0]

    other_msgs = max(0, total_messages - (client_msgs + pm_msgs + dev_msgs))

    tunnel_reachable = check_tunnel_reachable(tunnel_url) if tunnel_url else False

    return {
        "status": "online",
        "service": "Teams -> Jira Automation Engine",
        "uptime": round(time.time() - start_timestamp, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tunnel": {
            "active": tunnel_reachable,
            "url": tunnel_url,
            "reachable": tunnel_reachable,
        },
        "subscription": sub_info,
        "target": {
            "type": "Group Chat" if config.teams.chat_id else "Teams Channel",
            "id": config.teams.chat_id or config.teams.channel_id,
            "title": "Team To Jira Ticket Creation" if config.teams.chat_id else "Channel",
        },
        "roles": {
            "client": {"id": client_id, "name": "Dhruv dobariya (Client)"},
            "pm": {"id": pm_id, "name": "Santosh Yadav (PM)"},
            "developer": {"id": dev_id, "name": "Musaib Khan (Developer)"},
        },
        "pipeline": {
            "phase1": {"name": "Message Detection", "status": "active"},
            "phase2": {
                "name": "AI Ticket Extraction",
                "status": "active",
                "model": config.gemini.model,
                "geminiConfigured": bool(config.gemini.api_key),
            },
            "phase3": {"name": "PM Approval Workflow", "status": "active"},
            "phase4": {
                "name": "Jira Ticket Creation",
                "status": "active" if config.jira.is_configured else "pending_keys",
                "configured": config.jira.is_configured,
                "projectKey": config.jira.project_key,
                "baseUrl": config.jira.base_url,
            },
            "phase5": {
                "name": "Teams Confirmation Reply",
                "status": "active" if config.teams.webhook_url else "ready_for_webhook",
                "webhookConfigured": bool(config.teams.webhook_url),
            },
        },
        "metrics": {
            "total": total_messages,
            "client": client_msgs,
            "pm": pm_msgs,
            "developer": dev_msgs,
            "other": other_msgs,
        },
    }


@router.post("/api/messages/sync")
async def sync_messages():
    """Manually trigger backfill sync of recent messages from Microsoft Graph."""
    try:
        from src.services.message_service import sync_recent_messages
        result = await sync_recent_messages(top=20)
        return {"success": True, "result": result}
    except Exception as err:
        logger.error(f"Manual sync failed: {err}")
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/api/messages/{message_id}/extract-ticket")
async def extract_ticket_for_message(message_id: str):
    """Extract or re-extract structured Jira ticket fields from a stored message using Gemini."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    msg_dict = dict(row)
    sender_role = identify_sender_role(msg_dict.get("sender_user_id"), msg_dict.get("sender_display_name"))

    from src.services.ai_service import extract_jira_ticket
    from src.repositories.message_repository import update_message_ai_ticket

    ai_ticket = await extract_jira_ticket(
        msg_dict.get("message_text") or "",
        sender_name=msg_dict.get("sender_display_name"),
        sender_role=sender_role,
    )

    update_message_ai_ticket(message_id, ai_ticket)

    # Broadcast updated message to dashboard
    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "MESSAGE_UPDATED",
            "message": {
                "messageId": message_id,
                "aiTicket": ai_ticket,
            },
            "updated": True,
        })
    except Exception:
        pass

    return {"success": True, "messageId": message_id, "aiTicket": ai_ticket}


@router.post("/api/subscription/renew")
def renew_active_sub():
    """Manual trigger to renew or activate subscription from dashboard."""
    sub_info = get_active_subscription_info()
    if not sub_info.get("active"):
        # If no active subscription exists, attempt to create/activate it
        res = ensure_subscription_online()
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res.get("message", "Cannot activate subscription: Tunnel is offline"))
        return {"success": True, "subscription": res.get("subscription"), "action": "created"}

    sub_id = sub_info["id"]
    try:
        result = renew_subscription(sub_id, expiration_minutes=60)
        return {"success": True, "subscription": result, "action": "renewed"}
    except Exception as err:
        logger.error(f"Failed manual renewal: {err}")
        # If renewal fails (e.g. expired on remote), attempt fresh creation
        recreated = ensure_subscription_online()
        if recreated.get("status") != "error":
            return {"success": True, "subscription": recreated.get("subscription"), "action": "recreated"}
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/api/subscription/create")
def create_sub():
    """Manual trigger to create or recreate subscription from dashboard."""
    try:
        result = ensure_subscription_online()
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("message", "Failed to create subscription"))
        return {"success": True, "result": result}
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Failed manual creation: {err}")
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/api/subscription/delete")
def delete_sub():
    """Manual trigger to delete active subscription."""
    sub_info = get_active_subscription_info()
    if not sub_info.get("active"):
        return {"success": True, "message": "No active subscription"}

    sub_id = sub_info["id"]
    try:
        delete_subscription(sub_id)
        return {"success": True, "deletedId": sub_id}
    except Exception as err:
        logger.error(f"Failed deletion: {err}")
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/api/test/simulate")
async def simulate_message(req: SimulateMessageRequest):
    """Simulate an incoming message for testing dashboard and detection pipeline."""
    import base64
    from src.services.message_service import _message_attachment_cache

    role = req.role.upper()
    role_map = {
        "CLIENT": (config.roles.client, req.sender_name or "Dhruv dobariya"),
        "PM": (config.roles.pm, req.sender_name or "Santosh Yadav"),
        "DEVELOPER": (config.roles.developer, req.sender_name or "Musaib Khan"),
    }

    user_id, display_name = role_map.get(role, ("sim-user-" + str(uuid.uuid4())[:6], req.sender_name or "Test User"))
    sim_id = "sim-" + str(int(time.time() * 1000))

    sim_attachments = []
    sim_attachments_meta = []
    if req.attachment_base64:
        try:
            b_data = base64.b64decode(req.attachment_base64)
            att_name = req.attachment_name or "simulated_screenshot.png"
            att_type = req.attachment_type or "image/png"
            sim_attachments.append({
                "name": att_name,
                "content_type": att_type,
                "bytes": b_data,
            })
            sim_attachments_meta.append({
                "id": "att-" + sim_id,
                "contentType": att_type,
                "name": att_name,
                "contentUrl": None,
            })
            _message_attachment_cache[sim_id] = sim_attachments
        except Exception as b64_err:
            logger.warning(f"Could not decode simulated attachment base64: {b64_err}")

    sim_msg = {
        "messageId": sim_id,
        "chatId": config.teams.chat_id,
        "teamId": config.teams.team_id,
        "channelId": config.teams.channel_id,
        "sender": {
            "userId": user_id,
            "displayName": display_name,
        },
        "message": {
            "text": req.text,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "modifiedAt": None,
            "webUrl": None,
        },
        "replyToId": None,
        "attachments": sim_attachments_meta,
    }

    # Extract AI ticket draft if applicable
    has_att = len(sim_attachments) > 0
    if role == "CLIENT" or has_att or any(kw in req.text.lower() for kw in ["#issue", "#bug", "#task", "bug", "issue"]):
        try:
            from src.services.ai_service import extract_jira_ticket
            ai_ticket = await extract_jira_ticket(
                req.text,
                sender_name=display_name,
                sender_role=role,
                attachments=sim_attachments,
            )
            if ai_ticket.get("is_ticket_request"):
                sim_msg["aiTicket"] = ai_ticket
        except Exception:
            pass

    store_res = store_message(sim_msg)

    # Broadcast to dashboard
    await broadcast_message({
        "type": "NEW_MESSAGE",
        "message": sim_msg,
        "senderRole": role,
        "stored": store_res.get("stored", True),
        "duplicate": store_res.get("duplicate", False),
    })

    return {"success": True, "message": sim_msg, "role": role}


@router.post("/api/test/simulate-pm-approval/{message_id}")
async def simulate_pm_approval(message_id: str):
    """Simulate PM Santosh Yadav reacting with 👍 in Teams to test closed-loop ticket creation."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    msg = dict(row)
    pm_id = config.roles.pm or "d7bc3c28-33d9-4973-816e-445d51556b8b"
    pm_reaction = {
        "reactionType": "👍",
        "displayName": "Santosh Yadav",
        "userId": pm_id,
        "createdDateTime": datetime.now(timezone.utc).isoformat(),
    }

    raw_reactions = msg.get("reactions")
    reactions = []
    if raw_reactions:
        try:
            reactions = json.loads(raw_reactions) if isinstance(raw_reactions, str) else raw_reactions
        except Exception:
            reactions = []

    # Add reaction if not already there
    if not any((r.get("userId") == pm_id and (r.get("reactionType") in ["👍", "like", "heart", "thumbsup"])) for r in reactions):
        reactions.append(pm_reaction)

    db.execute("UPDATE messages SET reactions = ? WHERE message_id = ?", (json.dumps(reactions), message_id))

    normalized = {
        "messageId": message_id,
        "chatId": msg.get("chat_id"),
        "teamId": msg.get("team_id"),
        "channelId": msg.get("channel_id"),
        "sender": {"userId": msg.get("sender_user_id"), "displayName": msg.get("sender_display_name")},
        "message": {"text": msg.get("message_text")},
        "reactions": reactions,
    }

    # Closed-loop auto-creation
    from src.services.message_service import check_and_auto_create_jira_ticket
    sender_role = identify_sender_role(msg.get("sender_user_id"), msg.get("sender_display_name"))
    ticket_res = await check_and_auto_create_jira_ticket(normalized, sender_role)

    # Broadcast updated message
    await broadcast_message({
        "type": "MESSAGE_UPDATED",
        "message": normalized,
        "senderRole": sender_role,
        "stored": False,
        "duplicate": True,
        "updated": True,
    })

    return {"success": True, "ticket": ticket_res, "reactions": reactions}


@router.get("/api/jira/status")
async def get_jira_status():
    """Test and return live Jira Cloud connection status and project details."""
    from src.services.jira_service import test_jira_connection
    res = await test_jira_connection()
    return res


@router.post("/api/jira/test-ticket")
async def create_jira_test_ticket():
    """Create a quick test ticket in Jira to verify write permissions."""
    from src.services.jira_service import create_jira_issue
    res = await create_jira_issue(
        summary="[Test] Teams Automation Integration Test",
        description="### Verification Issue\nThis test ticket was created from the Teams-to-Jira automation dashboard to verify API connectivity and write permissions.",
        issue_type=config.jira.default_issue_type,
        priority="Medium",
        labels=["teams-automation", "manual-test"],
    )
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create ticket"))
    return res


@router.post("/api/jira/create-from-message/{message_id}")
async def create_jira_from_message(message_id: str):
    """Create a Jira ticket from an AI-extracted Teams message."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    msg = dict(row)
    raw_ai = msg.get("ai_ticket")
    ai_ticket = None
    if raw_ai:
        try:
            ai_ticket = json.loads(raw_ai) if isinstance(raw_ai, str) else raw_ai
        except Exception:
            ai_ticket = None

    # If no AI ticket yet, extract now
    if not ai_ticket or not ai_ticket.get("summary"):
        from src.services.ai_service import extract_jira_ticket
        sender_role = identify_sender_role(msg.get("sender_user_id"), msg.get("sender_display_name"))
        ai_ticket = await extract_jira_ticket(
            msg.get("message_text") or "",
            sender_name=msg.get("sender_display_name"),
            sender_role=sender_role,
        )

    from src.services.jira_service import create_jira_issue
    # Ensure reporter and message fields are populated in ticket data
    if not ai_ticket.get("reporter_name"):
        ai_ticket["reporter_name"] = msg.get("sender_display_name")
    if not ai_ticket.get("raw_message"):
        ai_ticket["raw_message"] = msg.get("message_text") or ""

    res = await create_jira_issue(
        summary=ai_ticket.get("summary", "Teams Issue Report"),
        description=ai_ticket.get("description", msg.get("message_text") or ""),
        issue_type=ai_ticket.get("issue_type", "Bug"),
        priority=ai_ticket.get("priority", "Medium"),
        labels=ai_ticket.get("labels", ["teams-automation"]),
        message_id=message_id,
        assignee_name=ai_ticket.get("suggested_assignee"),
        ticket_data=ai_ticket,
    )

    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create Jira issue"))

    # Post confirmation reply back to Teams
    try:
        from src.services.teams_notifier import send_ticket_created_notification
        await send_ticket_created_notification(
            ticket_key=res.get("key"),
            ticket_url=res.get("url"),
            summary=res.get("summary") or ai_ticket.get("summary", ""),
            issue_type=ai_ticket.get("issue_type", "Task"),
            priority=ai_ticket.get("priority", "Medium"),
            assignee=ai_ticket.get("suggested_assignee") or "Unassigned",
            reporter=msg.get("sender_display_name") or "Teams User",
            approval_note="Created via Automation Dashboard",
            chat_id=msg.get("chat_id"),
            team_id=msg.get("team_id"),
            channel_id=msg.get("channel_id"),
            parent_message_id=message_id,
        )
    except Exception as notify_err:
        logger.warning(f"Could not send Teams confirmation for manual ticket: {notify_err}")

    return res


@router.post("/api/teams/test-webhook")
async def test_teams_webhook():
    """Send a test card to the configured Microsoft Teams Webhook."""
    if not config.teams.webhook_url:
        raise HTTPException(
            status_code=400,
            detail="TEAMS_WEBHOOK_URL is not set in .env. Please configure it to test Teams notifications.",
        )
    from src.services.teams_notifier import send_ticket_created_notification
    res = await send_ticket_created_notification(
        ticket_key="TEST-1",
        ticket_url=config.jira.base_url or "https://dhruvdkombee.atlassian.net",
        summary="Test notification from Teams-to-Jira Automation",
        issue_type="Task",
        priority="Medium",
        assignee="Developer (Musaib Khan)",
        reporter="Client (Dhruv dobariya)",
        approval_note="Test message sent from Dashboard",
    )
    return res


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_connect(websocket)
    try:
        while True:
            # Keepalive listener
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_disconnect(websocket)
    except Exception:
        ws_disconnect(websocket)

