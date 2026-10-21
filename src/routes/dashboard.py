import time
import uuid
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel  
from fastapi import APIRouter, Request, Response, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse

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

    from src.services.member_sync_service import get_all_members_from_excel, get_active_pm_from_excel
    members = get_all_members_from_excel()
    client_m = next((m for m in members if (m.get("role") or "").upper() == "CLIENT"), None)
    pm_m = next((m for m in members if (m.get("role") or "").upper() == "PM"), None)
    dev_m = next((m for m in members if (m.get("role") or "").upper() == "DEVELOPER"), None)

    if client_m:
        client_id = client_m.get("user_id") or ""
        client_name = client_m.get("display_name") or "Client"
        client_assigned = True
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
            (client_id, client_id, f"%{client_name.split()[0].lower()}%"),
        )
        client_msgs = cursor.fetchone()[0]
    else:
        client_id = ""
        client_name = "Unassigned"
        client_assigned = False
        client_msgs = 0

    if pm_m:
        pm_id = pm_m.get("user_id") or ""
        pm_name = pm_m.get("display_name") or "Project Manager"
        pm_assigned = True
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
            (pm_id, pm_id, f"%{pm_name.split()[0].lower()}%"),
        )
        pm_msgs = cursor.fetchone()[0]
    else:
        pm_id = ""
        pm_name = "Unassigned"
        pm_assigned = False
        pm_msgs = 0

    if dev_m:
        dev_id = dev_m.get("user_id") or ""
        dev_name = dev_m.get("display_name") or "Developer"
        dev_assigned = True
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
            (dev_id, dev_id, f"%{dev_name.split()[0].lower()}%"),
        )
        dev_msgs = cursor.fetchone()[0]
    else:
        dev_id = ""
        dev_name = "Unassigned"
        dev_assigned = False
        dev_msgs = 0

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
            "client": {"id": client_id, "name": f"{client_name} (Client)" if client_assigned else "Unassigned", "assigned": client_assigned},
            "pm": {"id": pm_id, "name": f"{pm_name} (PM)" if pm_assigned else "Unassigned", "assigned": pm_assigned},
            "developer": {"id": dev_id, "name": f"{dev_name} (Developer)" if dev_assigned else "Unassigned", "assigned": dev_assigned},
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

    from src.services.member_sync_service import get_active_pm_from_excel, get_all_members_from_excel
    pm_info = get_active_pm_from_excel()
    members = get_all_members_from_excel()
    client_m = next((m for m in members if (m.get("role") or "").upper() == "CLIENT"), None)
    dev_m = next((m for m in members if (m.get("role") or "").upper() == "DEVELOPER"), None)

    role = (req.role or "CLIENT").upper()
    role_map = {
        "CLIENT": (client_m.get("user_id") if client_m else config.roles.client, req.sender_name or (client_m.get("display_name") if client_m else "Client User")),
        "PM": (pm_info.get("user_id") or config.roles.pm, req.sender_name or pm_info.get("name", "Project Manager")),
        "DEVELOPER": (dev_m.get("user_id") if dev_m else config.roles.developer, req.sender_name or (dev_m.get("display_name") if dev_m else "Lead Developer")),
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
    """Simulate PM reacting with 👍 in Teams to test closed-loop ticket creation."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    pm_id = pm_info.get("user_id") or config.roles.pm or "pm-user-id"
    pm_reaction = {
        "reactionType": "🎟️",
        "displayName": pm_info.get("name", "Project Manager"),
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
    from src.services.sender_service import is_ticket_approval_reaction
    if not any((r.get("userId") == pm_id and is_ticket_approval_reaction(r.get("reactionType"))) for r in reactions):
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


@router.post("/api/test/simulate-pm-disapproval/{message_id}")
async def simulate_pm_disapproval(message_id: str):
    """Simulate PM reacting with ❌ in Teams to test disapproval."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    pm_id = pm_info.get("user_id") or config.roles.pm or "pm-user-id"
    pm_reaction = {
        "reactionType": "❌",
        "displayName": pm_info.get("name", "Project Manager"),
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

    from src.services.sender_service import is_ticket_disapproval_reaction
    if not any((r.get("userId") == pm_id and is_ticket_disapproval_reaction(r.get("reactionType"))) for r in reactions):
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

    from src.services.message_service import check_and_auto_create_jira_ticket
    sender_role = identify_sender_role(msg.get("sender_user_id"), msg.get("sender_display_name"))
    decline_res = await check_and_auto_create_jira_ticket(normalized, sender_role)

    await broadcast_message({
        "type": "MESSAGE_UPDATED",
        "message": normalized,
        "senderRole": sender_role,
        "stored": False,
        "duplicate": True,
        "updated": True,
    })

    return {"success": True, "result": decline_res, "reactions": reactions}


# Re-exported approval helpers for backward compatibility (moved to src.routes.approvals)
from src.routes.approvals import (
    render_confirmation_html,
    render_assignee_dropdown_html,
    get_member_or_sender_identity,
    resolve_and_verify_approver,
    resolve_active_reviewer_from_request,
    get_browser_redirect_or_gateway,
    get_approver_name_for_message,
    render_identity_gateway_html,
)



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
            status=res.get("status") or "To Do",
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
        ticket_url=config.jira.base_url or "https://your-domain.atlassian.net",
        summary="Test notification from Teams-to-Jira Automation",
        issue_type="Task",
        priority="Medium",
        assignee="Lead Developer",
        reporter="Client Reporter",
        approval_note="Test message sent from Dashboard",
    )
@router.post("/api/messages/{message_id}/send-reminder")
async def trigger_message_pm_reminder(message_id: str):
    """Manually trigger the PM follow-up reminder (Teams @mention card + Outlook email) for testing."""
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    from src.services.reminder_service import check_and_send_message_reminder
    res = await check_and_send_message_reminder(row, force=True)
    return res


@router.post("/api/test/simulate-pm-followup/{message_id}")
async def simulate_pm_followup_by_id(message_id: str):
    """Trigger a test PM follow-up reminder for a specific message."""
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    from src.services.reminder_service import check_and_send_message_reminder
    res = await check_and_send_message_reminder(row, force=True)
    return {"success": True, "message_id": message_id, "result": res}


@router.post("/api/test/simulate-pm-followup")
async def simulate_pm_followup_latest():
    """Trigger a test PM follow-up reminder on the most recent client issue in the database."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM messages WHERE message_text IS NOT NULL AND message_text != '' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if not row:
        # Create a simulated test message first
        from src.services.message_service import process_incoming_chat_message
        sim_msg = {
            "messageId": f"sim-followup-{int(datetime.now().timestamp())}",
            "chatId": config.teams.chat_id,
            "sender": {"userId": "client-test-id", "displayName": "Client Reporter"},
            "message": {"text": "#issue Checkout button returns 500 error on payment page"},
            "createdDateTime": datetime.now(timezone.utc).isoformat(),
        }
        await process_incoming_chat_message(sim_msg)
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (sim_msg["messageId"],)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No message available for follow-up testing")

@router.post("/api/test/test-email")
async def test_email_endpoint(to_email: Optional[str] = None):
    """Test sending an alert email via Microsoft Graph / SMTP."""
    from src.services.email_service import send_pm_followup_email
    from src.services.reminder_service import get_active_pm
    pm_info = get_active_pm()
    target_email = to_email or pm_info.get("email") or "pm@example.com"

    res = await send_pm_followup_email(
        pm_email=target_email,
        pm_name=pm_info.get("name", "Project Manager"),
        reporter_name="Client (Test)",
        elapsed_minutes=15,
        issues=[{
            "summary": "Test issue for email verification",
            "issue_type": "Task",
            "priority": "High",
            "affected_module": "Authentication",
            "suggested_assignee": pm_info.get("name", "Lead Developer"),
            "observed_behavior": "This is a test notification to verify Outlook / SMTP email delivery."
        }],
        raw_message="Test message for email configuration verification",
        message_id="test-email-verification",
        created_at_str="Just now",
    )
    return {"to": target_email, "result": res}


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

