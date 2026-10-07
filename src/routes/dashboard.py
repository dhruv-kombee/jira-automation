import time
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse

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

    client_id = (client_m.get("user_id") if client_m else None) or config.roles.client or ""
    client_name = (client_m.get("display_name") if client_m else None) or "Client"
    pm_id = (pm_m.get("user_id") if pm_m else None) or config.roles.pm or ""
    pm_name = (pm_m.get("display_name") if pm_m else None) or "Project Manager"
    dev_id = (dev_m.get("user_id") if dev_m else None) or config.roles.developer or ""
    dev_name = (dev_m.get("display_name") if dev_m else None) or "Developer"

    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
        (client_id, client_id, f"%{client_name.split()[0].lower()}%"),
    )
    client_msgs = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
        (pm_id, pm_id, f"%{pm_name.split()[0].lower()}%"),
    )
    pm_msgs = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE (LOWER(sender_user_id) = LOWER(?) AND ? != '') OR LOWER(sender_display_name) LIKE ?",
        (dev_id, dev_id, f"%{dev_name.split()[0].lower()}%"),
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
            "client": {"id": client_id, "name": f"{client_name} (Client)"},
            "pm": {"id": pm_id, "name": f"{pm_name} (PM)"},
            "developer": {"id": dev_id, "name": f"{dev_name} (Developer)"},
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
    role = (req.role or "CLIENT").upper()
    role_map = {
        "CLIENT": (config.roles.client, req.sender_name or "Dhruv dobariya"),
        "PM": (pm_info.get("user_id") or config.roles.pm, req.sender_name or pm_info.get("name", "Project Manager")),
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
    """Simulate PM Santosh Yadav reacting with ❌ in Teams to test disapproval."""
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


def render_confirmation_html(
    title: str,
    status_type: str,  # "success", "declined", "error"
    heading: str,
    message: str,
    details: Optional[dict] = None,
    actions: Optional[list] = None,
) -> HTMLResponse:
    details = details or {}
    actions = actions or []

    badge_color = "#10b981" if status_type == "success" else ("#ef4444" if status_type == "declined" else "#f59e0b")
    badge_icon = "🎟️" if status_type == "success" else ("❌" if status_type == "declined" else "⚠️")

    facts_html = "".join(
        f'<div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #334155;">'
        f'<span style="color:#94a3b8; font-weight:500;">{k}</span>'
        f'<span style="color:#f8fafc; font-weight:600; text-align:right;">{v}</span>'
        f'</div>'
        for k, v in details.items()
    )

    actions_html = "".join(
        f'<a href="{act.get("url")}" target="_blank" style="display:inline-block; margin:6px; padding:10px 20px; background:#3b82f6; color:#ffffff; font-weight:600; border-radius:8px; text-decoration:none;">{act.get("label")}</a>'
        for act in actions
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
      box-sizing: border-box;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 520px;
      width: 100%;
      padding: 32px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      text-align: center;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: {badge_color}22;
      color: {badge_color};
      border: 1px solid {badge_color}44;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 10px 0;
      color: #ffffff;
    }}
    p {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 24px 0;
    }}
    .facts-box {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 24px;
      text-align: left;
      font-size: 13px;
    }}
    .footer-note {{
      font-size: 12px;
      color: #64748b;
      margin-top: 24px;
    }}
  </style>
  <script>
    // Automatically close tab after 1.5 seconds
    window.onload = function() {{
      setTimeout(function() {{
        try {{ window.close(); }} catch(e) {{}}
      }}, 1500);
    }};
  </script>
</head>
<body>
  <div class="card">
    <div class="badge">{badge_icon} {heading}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    {f'<div class="facts-box">{facts_html}</div>' if details else ''}
    <div>{actions_html}</div>
    <div style="margin-top:16px;">
      <button onclick="window.close()" style="background:#334155; color:#f8fafc; border:1px solid #475569; padding:8px 20px; border-radius:8px; cursor:pointer; font-weight:600; font-size:13px;">✕ Close Window</button>
    </div>
    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


def render_assignee_dropdown_html(
    message_id: str,
    summary: str,
    project_key: str,
    suggested_assignee: str,
    members: List[Dict[str, str]],
    approver: str,
    issue_idx: Optional[int] = None,
) -> HTMLResponse:
    """Render a dedicated, responsive Assignee Selection & Approval modal dialog."""
    options_html = []
    clean_suggested = suggested_assignee.strip().lower()

    for m in members:
        name = m.get("name", "")
        spec = m.get("specialty", "")
        # Match if suggested name overlaps
        is_sel = (name.lower() in clean_suggested or clean_suggested in name.lower())
        sel_attr = "selected" if is_sel else ""
        label = f"{name} — {spec}" if spec else name
        options_html.append(f'<option value="{name}" {sel_attr}>{label}</option>')

    # Add Unassigned option
    unassigned_sel = "selected" if clean_suggested in ("unassigned", "") else ""
    options_html.append(f'<option value="Unassigned" {unassigned_sel}>Unassigned</option>')

    options_joined = "\n          ".join(options_html)

    action_url = f"/api/jira/confirm-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/confirm-approval/{message_id}"
    reject_url = f"/api/jira/decline-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/decline-approval/{message_id}"
    issue_label = f"Issue #{issue_idx + 1}" if issue_idx is not None else "Jira Ticket"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Approve {issue_label} — Select Assignee</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 520px;
      width: 100%;
      padding: 30px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: #f59e0b22;
      color: #f59e0b;
      border: 1px solid #f59e0b44;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 10px 0;
      color: #ffffff;
    }}
    p.subtext {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 20px 0;
    }}
    .facts-box {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 20px;
      text-align: left;
      font-size: 13px;
    }}
    .fact-row {{
      display: flex;
      justify-content: space-between;
      padding: 6px 0;
      border-bottom: 1px solid #1e293b;
    }}
    .fact-row:last-child {{ border-bottom: none; }}
    .fact-label {{ color: #94a3b8; font-weight: 500; min-width: 110px; }}
    .fact-val {{ color: #f8fafc; font-weight: 600; text-align: right; word-break: break-word; }}
    .form-group {{
      text-align: left;
      margin-bottom: 22px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #cbd5e1;
      margin-bottom: 8px;
    }}
    select {{
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1.5px solid #3b82f6;
      border-radius: 8px;
      color: #f8fafc;
      font-size: 14px;
      font-weight: 500;
      outline: none;
      cursor: pointer;
    }}
    select:focus {{
      border-color: #60a5fa;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
    }}
    .btn-approve {{
      width: 100%;
      padding: 13px 20px;
      background: #10b981;
      color: #ffffff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: background 0.15s ease;
    }}
    .btn-approve:hover {{
      background: #059669;
    }}
    .btn-reject {{
      display: inline-block;
      margin-top: 14px;
      color: #ef4444;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
    }}
    .btn-reject:hover {{
      text-decoration: underline;
    }}
    .footer-note {{
      font-size: 12px;
      color: #64748b;
      margin-top: 22px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">📋 {issue_label} Approval Required</div>
    <h1>Confirm {issue_label} Creation</h1>
    <p class="subtext">Select the developer who should be assigned to this ticket before creating it in Jira.</p>

    <div class="facts-box">
      <div class="fact-row">
        <span class="fact-label">Topic Details:</span>
        <span class="fact-val">{summary}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">Scrum Project:</span>
        <span class="fact-val">{project_key}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">AI Suggestion:</span>
        <span class="fact-val" style="color:#60a5fa;">{suggested_assignee}</span>
      </div>
    </div>

    <form method="POST" action="{action_url}">
      <div class="form-group">
        <label for="assigneeSelect">👤 Assignee Dropdown:</label>
        <select name="assignee" id="assigneeSelect">
          {options_joined}
        </select>
      </div>

      <button type="submit" class="btn-approve">
        🚀 Confirm & Create {issue_label}
      </button>
    </form>

    <div style="text-align: center;">
      <a href="{reject_url}" class="btn-reject">❌ Reject {issue_label} Creation</a>
    </div>

    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@router.api_route("/api/jira/confirm-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_approval_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    auto: Optional[str] = None,
):
    """PM Approval endpoint with interactive Assignee Dropdown and 1-Click execution."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.services.message_service import execute_jira_ticket_creation
    from src.services.member_sync_service import get_active_pm_from_excel, get_all_members_from_excel
    from src.database import get_db
    import json
    import re

    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    # If auto=1, execute ticket creation immediately with chosen or suggested assignee
    if auto in ("1", "true", "yes"):
        res = await execute_jira_ticket_creation(
            message_id, approver_name=approver, assignee_override=assignee
        )
        if not res.get("success"):
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", "Failed to create Jira ticket"),
            )
        key = res.get("key", "Created")
        url = res.get("url", "#")
        already = res.get("already_existed", False)
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title=f"Jira Ticket {'Already Active' if already else 'Created Successfully'}",
            status_type="success",
            heading="Approved by PM",
            message=f"Ticket {key} has been created and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )

    # Otherwise: Render interactive Assignee Dropdown modal page
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
        )

    if row["jira_issue_key"]:
        return render_confirmation_html(
            title="Ticket Already Created",
            status_type="success",
            heading="Active in Jira",
            message=f"Ticket {row['jira_issue_key']} has already been created for this issue.",
            details={"Jira Ticket": row["jira_issue_key"], "Status": "Active"},
            actions=[{"label": f"Open {row['jira_issue_key']} in Jira ↗", "url": row["jira_issue_url"] or "#"}],
        )

    ai_ticket = {}
    if row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            ai_ticket = {}

    summary = ai_ticket.get("summary") or row["message_text"] or "Issue Report"
    project_key = config.jira.project_key or "SCRUM"
    suggested_assignee = assignee or ai_ticket.get("suggested_assignee") or "Santosh Yadav"

    # Get assignable members from Member.xlsx
    try:
        raw_members = get_all_members_from_excel()
    except Exception:
        raw_members = []

    assignable_list = []
    seen = set()
    for m in raw_members:
        r = (m.get("role") or "").upper()
        if r != "CLIENT":
            c_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip()
            if c_name and c_name not in seen:
                seen.add(c_name)
                assignable_list.append({
                    "name": c_name,
                    "specialty": m.get("specialty") or r,
                    "role": r,
                })

    if not assignable_list:
        assignable_list = [
            {"name": "Santosh Yadav", "specialty": "Backend & API Lead", "role": "DEVELOPER"},
            {"name": "Musaib Khan", "specialty": "Frontend & UI Lead", "role": "DEVELOPER"},
            {"name": "Nishi Sharma", "specialty": "AI Developer", "role": "DEVELOPER"},
            {"name": "Hemil Ghori", "specialty": "Project Manager / Scrum Master", "role": "PM"},
        ]

    return render_assignee_dropdown_html(
        message_id=message_id,
        summary=summary,
        project_key=project_key,
        suggested_assignee=suggested_assignee,
        members=assignable_list,
        approver=approver,
    )


@router.post("/api/jira/confirm-approval/{message_id}")
async def confirm_approval_post(
    message_id: str,
    request: Request,
):
    """Programmatic / Web Form PM Approval endpoint (POST)."""
    from src.services.message_service import execute_jira_ticket_creation
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    assignee = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
        except Exception:
            pass

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, assignee_override=assignee
    )
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", "Failed to create Jira ticket"),
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create ticket"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title="Jira Ticket Created Successfully",
            status_type="success",
            heading="Approved by PM",
            message=f"Ticket {key} has been created and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )
    return res


@router.api_route("/api/jira/decline-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_approval_get(message_id: str, request: Request):
    """1-Click PM Decline/Reject endpoint for Teams card action links (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.services.message_service import execute_jira_ticket_decline
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver)
    if not res.get("success"):
        return render_confirmation_html(
            title="Reject Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", "Failed to reject ticket"),
        )
    return render_confirmation_html(
        title="Ticket Creation Rejected",
        status_type="declined",
        heading="Rejected by PM",
        message="Ticket creation was rejected. No tickets were created in Jira, and notification has been posted to Teams. You can close this window now.",
        details={"Status": "Rejected", "Rejected By": approver},
    )


@router.post("/api/jira/decline-approval/{message_id}")
async def decline_approval_post(message_id: str):
    """Programmatic / Dashboard PM Decline endpoint (POST)."""
    from src.services.message_service import execute_jira_ticket_decline
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline ticket"))
    return res


@router.api_route("/api/jira/confirm-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_all_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await confirm_approval_get(message_id, request, assignee=assignee, auto=auto)


@router.post("/api/jira/confirm-issue/{message_id}")
async def confirm_issue_all_post(message_id: str, request: Request):
    """Programmatic PM Approval for all issues in a message (POST)."""
    return await confirm_approval_post(message_id, request)


@router.api_route("/api/jira/decline-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_issue_all_get(message_id: str, request: Request):
    """1-Click PM Decline for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await decline_approval_get(message_id, request)


@router.post("/api/jira/decline-issue/{message_id}")
async def decline_issue_all_post(message_id: str):
    """Programmatic PM Decline for all issues in a message (POST)."""
    return await decline_approval_post(message_id)


@router.api_route("/api/jira/confirm-issue/{message_id}/{issue_idx}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_get(
    message_id: str,
    issue_idx: int,
    request: Request,
    assignee: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval or Assignee Dropdown for a specific single issue (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.services.message_service import execute_jira_ticket_creation
    from src.services.member_sync_service import get_active_pm_from_excel, get_all_members_from_excel
    from src.database import get_db
    import json
    import re

    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    # If auto=1, execute ticket creation immediately with chosen or suggested assignee
    if auto in ("1", "true", "yes"):
        res = await execute_jira_ticket_creation(
            message_id, approver_name=approver, issue_idx=issue_idx, assignee_override=assignee
        )
        if not res.get("success"):
            return render_confirmation_html(
                title="Issue Creation Failed",
                status_type="error",
                heading="Action Required",
                message=res.get("error", f"Failed to create Issue #{issue_idx + 1}"),
            )
        key = res.get("key", "Created")
        url = res.get("url", "#")
        already = res.get("already_existed", False)
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} {'Already Active' if already else 'Created'} in Jira",
            status_type="success",
            heading="Ticket Active in Jira" if already else f"Issue #{issue_idx + 1} Approved & Created",
            message=f"Ticket {key} {'is already active in Jira' if already else f'was successfully created in Jira for Issue #{issue_idx + 1} and assigned to {target_dev}'}.",
            details={"Jira Ticket": key, "Status": "Active in Jira", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )

    # Otherwise: Render interactive Assignee Dropdown modal page for this specific issue
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
        )

    ai_ticket = {}
    if row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            ai_ticket = {}

    issues = ai_ticket.get("issues", [])
    if not issues or not isinstance(issues, list):
        issues = [ai_ticket]

    if not (0 <= issue_idx < len(issues)):
        return render_confirmation_html(
            title="Invalid Issue",
            status_type="error",
            heading="Error",
            message=f"Issue #{issue_idx + 1} was not found in this message.",
        )

    target_issue = issues[issue_idx]
    if target_issue.get("status") == "APPROVED" or target_issue.get("jira_key"):
        active_key = target_issue.get("jira_key") or row["jira_issue_key"]
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Already Created",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Active in Jira",
            message=f"Ticket {active_key} has already been created for Issue #{issue_idx + 1}.",
            details={"Jira Ticket": active_key, "Status": "Active"},
            actions=[{"label": f"Open {active_key} in Jira ↗", "url": target_issue.get("jira_url") or row["jira_issue_url"] or "#"}],
        )

    if target_issue.get("status") == "DECLINED":
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Declined",
            status_type="declined",
            heading=f"Issue #{issue_idx + 1} Declined",
            message=f"Issue #{issue_idx + 1} was previously declined by PM and cannot be created.",
            details={"Status": "Declined", "Declined By": target_issue.get("declined_by", "PM")},
        )

    summary = target_issue.get("summary") or f"Issue #{issue_idx + 1}"
    project_key = config.jira.project_key or "SCRUM"
    suggested_assignee = assignee or target_issue.get("suggested_assignee") or "Santosh Yadav"

    try:
        raw_members = get_all_members_from_excel()
    except Exception:
        raw_members = []

    assignable_list = []
    seen = set()
    for m in raw_members:
        r = (m.get("role") or "").upper()
        if r != "CLIENT":
            c_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip()
            if c_name and c_name not in seen:
                seen.add(c_name)
                assignable_list.append({
                    "name": c_name,
                    "specialty": m.get("specialty") or r,
                    "role": r,
                })

    if not assignable_list:
        assignable_list = [
            {"name": "Santosh Yadav", "specialty": "Backend & API Lead", "role": "DEVELOPER"},
            {"name": "Musaib Khan", "specialty": "Frontend & UI Lead", "role": "DEVELOPER"},
            {"name": "Nishi Sharma", "specialty": "AI Developer", "role": "DEVELOPER"},
            {"name": "Hemil Ghori", "specialty": "Project Manager / Scrum Master", "role": "PM"},
        ]

    return render_assignee_dropdown_html(
        message_id=message_id,
        summary=summary,
        project_key=project_key,
        suggested_assignee=suggested_assignee,
        members=assignable_list,
        approver=approver,
        issue_idx=issue_idx,
    )


@router.post("/api/jira/confirm-issue/{message_id}/{issue_idx}")
async def confirm_issue_post(
    message_id: str,
    issue_idx: int,
    request: Request,
):
    """Programmatic / Dashboard PM Approval for a specific single issue (POST)."""
    from src.services.message_service import execute_jira_ticket_creation
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    assignee = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
        except Exception:
            pass

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, issue_idx=issue_idx, assignee_override=assignee
    )
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", f"Failed to create Issue #{issue_idx + 1}"),
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create Jira issue"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Created Successfully",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Approved by PM",
            message=f"Ticket {key} has been created in Jira and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )
    return res


@router.api_route("/api/jira/decline-issue/{message_id}/{issue_idx}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_issue_get(
    message_id: str,
    issue_idx: int,
    request: Request,
):
    """1-Click PM Decline for a specific single issue in a multi-issue triage card (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.services.message_service import execute_jira_ticket_decline
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, issue_idx=issue_idx)
    if not res.get("success"):
        return render_confirmation_html(
            title="Action Failed",
            status_type="error",
            heading="Cannot Reject",
            message=res.get("error", "Failed to reject issue"),
        )
    return render_confirmation_html(
        title=f"Issue #{issue_idx + 1} Rejected",
        status_type="declined",
        heading=f"Issue #{issue_idx + 1} Rejected by PM",
        message=f"Issue #{issue_idx + 1} was rejected. No Jira ticket was created for this issue. You can close this window now.",
        details={"Status": f"Issue #{issue_idx + 1} Rejected", "Rejected By": approver},
    )


@router.post("/api/jira/decline-issue/{message_id}/{issue_idx}")
async def decline_issue_post(message_id: str, issue_idx: int):
    """Programmatic / Dashboard PM Decline for a specific single issue (POST)."""
    from src.services.message_service import execute_jira_ticket_decline
    from src.services.member_sync_service import get_active_pm_from_excel
    pm_info = get_active_pm_from_excel()
    approver = f"PM {pm_info.get('name', 'Project Manager')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, issue_idx=issue_idx)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline Jira issue"))
    return res


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
            "sender": {"userId": "client-test-id", "displayName": "Dhruv dobariya"},
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
    target_email = to_email or pm_info.get("email") or "santosh.yadav@kombee.com"

    res = await send_pm_followup_email(
        pm_email=target_email,
        pm_name=pm_info.get("name", "Santosh Yadav"),
        reporter_name="Client (Test)",
        elapsed_minutes=15,
        issues=[{
            "summary": "Test issue for email verification",
            "issue_type": "Task",
            "priority": "High",
            "affected_module": "Authentication",
            "suggested_assignee": "Musaib Khan",
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

