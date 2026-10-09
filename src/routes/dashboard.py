import time
import uuid
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel  
from fastapi import APIRouter, Request, Response, HTTPException, WebSocket
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


def render_confirmation_html(
    title: str,
    status_type: str,  # "success", "declined", "error"
    heading: str,
    message: str,
    details: Optional[dict] = None,
    actions: Optional[list] = None,
    status_code: int = 200,
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

    auto_close_script = ""
    if status_type in ("success", "declined") and status_code < 400:
        auto_close_script = """
  <script>
    // Automatically close tab after 1 seconds
    window.onload = function() {
      setTimeout(function() {
        try { window.close(); } catch(e) {}
      }, 2500);
    };
  </script>"""

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
      margin-bottom: 20px;
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
  {auto_close_script}
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
    return HTMLResponse(content=html_content, status_code=status_code)


def resolve_and_verify_approver(approver_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verify that the provided approver identifier belongs to an active, authorized management member (TL, PM, HM)."""
    if not approver_identifier:
        return None
    from src.services.member_sync_service import get_member_by_id_or_name
    from src.services.sender_service import is_user_authorized_approver

    member = get_member_by_id_or_name(user_id=approver_identifier, display_name=approver_identifier)
    if not member:
        return None

    # Check if member is authorized to approve (explicitly disallow client self-approval on the web form)
    if not is_user_authorized_approver(user_id=member.get("user_id"), display_name=member.get("display_name"), allow_client=False):
        return None

    return member


def get_approver_name_for_message(row: Optional[Any] = None, explicit_reviewer: Optional[str] = None) -> str:
    """Resolve an authorized approver name string (e.g. 'TL', 'PM ', 'HM ')."""
    from src.services.member_sync_service import get_active_pm_from_excel, get_member_by_id_or_name
    import json

    # 1. If explicit reviewer provided and valid
    if explicit_reviewer:
        m = get_member_by_id_or_name(user_id=explicit_reviewer, display_name=explicit_reviewer)
        if m:
            m_role = (m.get("role") or "PM").upper().strip()
            return f"{m_role} {m.get('display_name')}"

    # 2. Check reactions stored on the message for an authorized manager (TL, PM, HM)
    if row:
        reactions_raw = row["reactions"] if isinstance(row, dict) or hasattr(row, "__getitem__") else None
        if reactions_raw:
            try:
                reactions = json.loads(reactions_raw) if isinstance(reactions_raw, str) else reactions_raw
                if isinstance(reactions, list):
                    for r in reactions:
                        u_id = r.get("userId")
                        d_name = r.get("displayName")
                        m = get_member_by_id_or_name(user_id=u_id, display_name=d_name)
                        if m:
                            m_role = (m.get("role") or "").upper().strip()
                            m_level = str(m.get("level") or "").upper().strip()
                            if m_role in ("TL", "PM", "HM") or m_level in ("LEVEL 1", "LEVEL 2", "LEVEL 3"):
                                return f"{m_role} {m.get('display_name')}"
            except Exception:
                pass

    # 3. Fallback to active PM from Member.xlsx
    active_pm = get_active_pm_from_excel()
    pm_name = active_pm.get("name") or active_pm.get("display_name") or "Project Manager"
    return f"PM {pm_name}"


def render_decline_dropdown_html(
    message_id: str,
    summary: str,
    project_key: str,
    issue_idx: Optional[int] = None,
) -> HTMLResponse:
    """Render a dedicated, responsive Decline/Reject modal dialog requiring authorized approver selection."""
    from src.services.member_sync_service import get_active_authorized_approvers
    authorized_approvers = get_active_authorized_approvers()
    approvers_options_html = []
    for ap in authorized_approvers:
        ap_id = ap.get("user_id") or ap.get("display_name")
        ap_name = re.sub(r"\s+", " ", ap.get("display_name", "")).strip()
        ap_role = (ap.get("role") or "").upper().strip()
        ap_lvl = ap.get("level", "")
        lvl_str = f" - {ap_lvl}" if ap_lvl and ap_lvl != "-" else ""
        approvers_options_html.append(
            f'<option value="{ap_id}">{ap_name} ({ap_role}{lvl_str})</option>'
        )
    approvers_joined = "\n          ".join(approvers_options_html)

    action_url = f"/api/jira/decline-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/decline-approval/{message_id}"
    issue_label = f"Issue #{issue_idx + 1}" if issue_idx is not None else "Ticket"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reject {issue_label} — Confirmation</title>
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
      background: #ef444422;
      color: #ef4444;
      border: 1px solid #ef444444;
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
    select, input[type="text"] {{
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1.5px solid #ef4444;
      border-radius: 8px;
      color: #f8fafc;
      font-size: 14px;
      font-weight: 500;
      outline: none;
    }}
    select:focus, input[type="text"]:focus {{
      border-color: #f87171;
      box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.2);
    }}
    .btn-decline {{
      width: 100%;
      padding: 13px 20px;
      background: #ef4444;
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
    .btn-decline:hover {{
      background: #dc2626;
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
    <div class="badge">❌ Reject {issue_label} Confirmation</div>
    <h1>Confirm Rejection of {issue_label}</h1>
    <p class="subtext">Select your authorized management identity to confirm rejection. No tickets will be created in Jira.</p>

    <div class="facts-box">
      <div class="fact-row">
        <span class="fact-label">Topic Details:</span>
        <span class="fact-val">{summary}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">Scrum Project:</span>
        <span class="fact-val">{project_key}</span>
      </div>
    </div>

    <form method="POST" action="{action_url}">
      <div class="form-group">
        <label for="approverSelect">🛡️ Confirm Your Management Identity (TL / PM / HM):</label>
        <select name="approver_id" id="approverSelect" required>
          <option value="" disabled selected>-- Select your name from authorized roster --</option>
          {approvers_joined}
        </select>
        <span style="font-size: 11px; color: #94a3b8; display: block; margin-top: 4px;">
          ⚠️ Verification Required: Only active Team Leads, Project Managers, and Higher Management can decline tickets.
        </span>
      </div>

      <div class="form-group">
        <label for="reasonInput">📝 Reason for Decline (Optional):</label>
        <input type="text" name="reason" id="reasonInput" placeholder="e.g. Expected behavior, duplicate report, insufficient info" />
      </div>

      <button type="submit" class="btn-decline">
        ❌ Confirm Rejection
      </button>
    </form>

    <div style="text-align: center; margin-top: 14px;">
      <a href="javascript:history.back()" style="color:#94a3b8; text-decoration:none; font-size:13px;">← Go Back</a>
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
    all_roster_members: Optional[List[Dict[str, Any]]] = None,
    default_reviewer: Optional[str] = None,
) -> HTMLResponse:
    """Render a dedicated, responsive Assignee Selection & Role-Verified Approval modal dialog."""
    import re
    if all_roster_members is None:
        from src.services.member_sync_service import get_all_members_from_excel
        try:
            all_roster_members = get_all_members_from_excel()
        except Exception:
            all_roster_members = []

    options_html = []
    clean_suggested = suggested_assignee.strip().lower()

    for m in members:
        name = m.get("name", "")
        spec = m.get("specialty", "")
        is_sel = (name.lower() in clean_suggested or clean_suggested in name.lower())
        sel_attr = "selected" if is_sel else ""
        label = f"{name} — {spec}" if spec else name
        options_html.append(f'<option value="{name}" {sel_attr}>{label}</option>')

    unassigned_sel = "selected" if clean_suggested in ("unassigned", "") else ""
    options_html.append(f'<option value="Unassigned" {unassigned_sel}>Unassigned</option>')
    options_joined = "\n          ".join(options_html)

    # Determine initial selected reviewer from default_reviewer (typically the message reporter e.g. Dhruv dobariya)
    clean_default_reviewer = (default_reviewer or "").strip().lower()
    init_member = None
    for rm in all_roster_members:
        rm_name = re.sub(r"\s+", " ", rm.get("display_name", "")).strip()
        if clean_default_reviewer and (clean_default_reviewer in rm_name.lower() or rm_name.lower() in clean_default_reviewer):
            init_member = rm
            break

    if not init_member and all_roster_members:
        for rm in all_roster_members:
            if (rm.get("role") or "").upper() == "CLIENT":
                init_member = rm
                break
        if not init_member:
            init_member = all_roster_members[0]

    reviewers_html = []
    for rm in all_roster_members:
        rm_name = re.sub(r"\s+", " ", rm.get("display_name", "")).strip()
        rm_role = (rm.get("role") or "").upper().strip()
        rm_lvl = rm.get("level", "").strip()
        lvl_str = f" - {rm_lvl}" if rm_lvl and rm_lvl != "-" else ""
        rm_id = rm.get("user_id") or rm_name
        can_appr = bool(rm.get("can_approve")) and rm_role in ("TL", "PM", "HM")
        can_appr_attr = "1" if can_appr else "0"

        is_sel = (init_member and (rm.get("user_id") == init_member.get("user_id") or rm_name.lower() == re.sub(r"\s+", " ", init_member.get("display_name", "")).strip().lower()))
        sel_attr = "selected" if is_sel else ""

        label = f"{rm_name} — {rm_role}{lvl_str}"
        reviewers_html.append(
            f'<option value="{rm_id}" data-can-approve="{can_appr_attr}" data-role="{rm_role}" data-name="{rm_name}" {sel_attr}>{label}</option>'
        )

    reviewers_joined = "\n          ".join(reviewers_html)

    # Initial server-side state for banner, button, and reject link
    init_role = (init_member.get("role") if init_member else "").upper().strip()
    init_name = re.sub(r"\s+", " ", init_member.get("display_name", "")).strip() if init_member else "Reviewer"
    init_can_approve = bool(init_member.get("can_approve")) and init_role in ("TL", "PM", "HM") if init_member else False

    issue_label = f"Issue #{issue_idx + 1}" if issue_idx is not None else "Jira Ticket"
    action_url = f"/api/jira/confirm-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/confirm-approval/{message_id}"
    reject_url = f"/api/jira/decline-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/decline-approval/{message_id}"

    if not init_can_approve:
        initial_banner_html = f"""<div class="alert-box alert-danger">
          <div class="alert-icon">⛔</div>
          <div class="alert-body">
            <div class="alert-title">No Authority to Confirm</div>
            <div class="alert-desc">
              You are currently identified as <strong>{init_name} ({init_role or 'Client'})</strong>.<br>
              Clients and Developers do not have authority to confirm or reject Jira tickets.<br>
              <strong>Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM) to review and approve.</strong>
            </div>
          </div>
        </div>"""
        init_btn_disabled = "disabled"
        init_btn_class = "btn-disabled"
        init_btn_text = "🔒 Management Authority Required (TL, PM, HM)"
        init_reject_class = "btn-reject-disabled"
    else:
        initial_banner_html = f"""<div class="alert-box alert-success">
          <div class="alert-icon">🛡️</div>
          <div class="alert-body">
            <div class="alert-title">Authorized Management Reviewer</div>
            <div class="alert-desc">
              Identified as <strong>{init_name} ({init_role})</strong>. You have authority to confirm and assign this ticket in Jira Cloud.
            </div>
          </div>
        </div>"""
        init_btn_disabled = ""
        init_btn_class = ""
        init_btn_text = f"🚀 Confirm & Create {issue_label}"
        init_reject_class = ""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Approve {issue_label} — Role Authority Verification</title>
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
      margin: 0 0 8px 0;
      color: #ffffff;
    }}
    p.subtext {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 18px 0;
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
      margin-bottom: 18px;
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
    .alert-box {{
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 18px;
      display: flex;
      gap: 12px;
      align-items: flex-start;
      text-align: left;
      font-size: 13px;
      line-height: 1.5;
    }}
    .alert-danger {{
      background: #451a1a;
      border: 1px solid #ef4444;
      color: #fca5a5;
    }}
    .alert-danger .alert-title {{
      font-weight: 700;
      color: #ef4444;
      margin-bottom: 4px;
    }}
    .alert-success {{
      background: #064e3b;
      border: 1px solid #10b981;
      color: #a7f3d0;
    }}
    .alert-success .alert-title {{
      font-weight: 700;
      color: #34d399;
      margin-bottom: 4px;
    }}
    .alert-icon {{
      font-size: 18px;
      line-height: 1;
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
      transition: all 0.15s ease;
    }}
    .btn-approve:hover:not(:disabled) {{
      background: #059669;
    }}
    .btn-disabled {{
      background: #334155 !important;
      color: #94a3b8 !important;
      cursor: not-allowed !important;
      border: 1px solid #475569 !important;
      opacity: 0.8;
    }}
    .btn-reject {{
      display: inline-block;
      margin-top: 14px;
      color: #ef4444;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      transition: color 0.15s;
    }}
    .btn-reject:hover {{
      text-decoration: underline;
    }}
    .btn-reject-disabled {{
      color: #64748b !important;
      cursor: not-allowed !important;
      text-decoration: none !important;
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
    <div class="badge">📋 {issue_label} Approval Verification</div>
    <h1>Confirm {issue_label} Creation</h1>
    <p class="subtext">Verify approver authority and choose developer assignment:</p>

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

    <form method="POST" action="{action_url}" id="approvalForm">
      <div class="form-group">
        <label for="reviewerSelect">🛡️ Reviewer Identity (Who is confirming?):</label>
        <select name="reviewer" id="reviewerSelect" onchange="onReviewerChange()">
          {reviewers_joined}
        </select>
      </div>

      <div id="authorityBanner">
        {initial_banner_html}
      </div>

      <div class="form-group" id="assigneeGroup">
        <label for="assigneeSelect">👤 Assignee Dropdown:</label>
        <select name="assignee" id="assigneeSelect">
          {options_joined}
        </select>
      </div>

      <button type="submit" id="approveBtn" class="btn-approve {init_btn_class}" {init_btn_disabled}>
        {init_btn_text}
      </button>
    </form>

    <div style="text-align: center;">
      <a href="{reject_url}" id="rejectBtn" class="btn-reject {init_reject_class}" onclick="return onRejectClick(event)">❌ Reject {issue_label} Creation</a>
    </div>

    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>

  <script>
    function getSelectedReviewerMeta() {{
      const sel = document.getElementById('reviewerSelect');
      if (!sel || sel.selectedIndex < 0) return {{ canApprove: false, role: '', name: '' }};
      const opt = sel.options[sel.selectedIndex];
      return {{
        canApprove: opt.getAttribute('data-can-approve') === '1',
        role: opt.getAttribute('data-role') || '',
        name: opt.getAttribute('data-name') || opt.text.split('—')[0].trim()
      }};
    }}

    function onReviewerChange() {{
      const sel = document.getElementById('reviewerSelect');
      const meta = getSelectedReviewerMeta();
      try {{ localStorage.setItem('saved_jira_reviewer', sel.value); }} catch(e) {{}}

      const banner = document.getElementById('authorityBanner');
      const approveBtn = document.getElementById('approveBtn');
      const rejectBtn = document.getElementById('rejectBtn');

      if (!meta.canApprove) {{
        banner.innerHTML = `
          <div class="alert-box alert-danger">
            <div class="alert-icon">⛔</div>
            <div class="alert-body">
              <div class="alert-title">No Authority to Confirm</div>
              <div class="alert-desc">
                You are currently identified as <strong>${{meta.name}} (${{meta.role || 'Client'}})</strong>.<br>
                You have no authority to confirm or reject Jira tickets.<br>
                <strong>Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).</strong>
              </div>
            </div>
          </div>
        `;
        approveBtn.disabled = true;
        approveBtn.className = 'btn-approve btn-disabled';
        approveBtn.innerHTML = '🔒 Management Authority Required (TL, PM, HM)';
        if (rejectBtn) {{
          rejectBtn.className = 'btn-reject btn-reject-disabled';
        }}
      }} else {{
        banner.innerHTML = `
          <div class="alert-box alert-success">
            <div class="alert-icon">🛡️</div>
            <div class="alert-body">
              <div class="alert-title">Authorized Management Reviewer</div>
              <div class="alert-desc">
                Identified as <strong>${{meta.name}} (${{meta.role}})</strong>. You have authority to confirm and assign this ticket in Jira Cloud.
              </div>
            </div>
          </div>
        `;
        approveBtn.disabled = false;
        approveBtn.className = 'btn-approve';
        approveBtn.innerHTML = '🚀 Confirm & Create {issue_label}';
        if (rejectBtn) {{
          rejectBtn.className = 'btn-reject';
        }}
      }}
    }}

    function onRejectClick(e) {{
      const meta = getSelectedReviewerMeta();
      if (!meta.canApprove) {{
        e.preventDefault();
        alert('You have no authority to reject this ticket. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).');
        return false;
      }}
      return true;
    }}

    window.addEventListener('DOMContentLoaded', function() {{
      try {{
        const saved = localStorage.getItem('saved_jira_reviewer');
        const sel = document.getElementById('reviewerSelect');
        if (saved && sel) {{
          for (let i = 0; i < sel.options.length; i++) {{
            if (sel.options[i].value === saved) {{
              sel.selectedIndex = i;
              break;
            }}
          }}
        }}
      }} catch(e) {{}}
      onReviewerChange();
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@router.api_route("/api/jira/confirm-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_approval_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM/TL Approval endpoint for Teams card action links (GET).
    Directly creates Jira ticket, displays success confirmation card, and auto-closes window.
    """
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation
    import json

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    if row["jira_issue_key"]:
        key = row["jira_issue_key"]
        url = row["jira_issue_url"] or "#"
        return render_confirmation_html(
            title="Ticket Already Created",
            status_type="success",
            heading="Active in Jira",
            message=f"Ticket {key} has already been created for this issue. You can close this window now.",
            details={"Jira Ticket": key, "Status": "Active"},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )

    if reviewer:
        member = resolve_and_verify_approver(reviewer)
        if not member:
            return render_confirmation_html(
                title="No Authority to Confirm",
                status_type="error",
                heading="Authority Check Failed",
                message="You have no authority to confirm Jira ticket creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    else:
        approver = get_approver_name_for_message(row=row)

    target_assignee = assignee
    if not target_assignee and row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
            target_assignee = ai_ticket.get("suggested_assignee")
        except Exception:
            pass

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, assignee_override=target_assignee
    )
    if not res.get("success"):
        return render_confirmation_html(
            title="Action Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", "Failed to create Jira ticket"),
            status_code=400,
        )

    key = res.get("key", "Created")
    url = res.get("url", "#")
    already = res.get("already_existed", False)
    display_dev = target_assignee or "Assigned Developer"

    return render_confirmation_html(
        title=f"Jira Ticket {'Already Active' if already else 'Created Successfully'}",
        status_type="success",
        heading=f"Approved by {approver}",
        message=f"Ticket {key} has been created and assigned to {display_dev}. Confirmation posted to Teams. You can close this window now.",
        details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": display_dev, "Approved By": approver},
        actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
    )


@router.post("/api/jira/confirm-approval/{message_id}")
async def confirm_approval_post(
    message_id: str,
    request: Request,
):
    """Role-verified PM/TL Approval endpoint (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation

    assignee = None
    approver_id = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
        except Exception:
            pass

    if not approver_id and not assignee:
        # Programmatic / Dashboard trigger without explicit reviewer
        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Message not found")
        approver = get_approver_name_for_message(row=row)
    else:
        # Strictly verify that reviewer is an authorized management member (TL, PM, HM)
        member = resolve_and_verify_approver(approver_id)
        if not member:
            logger.warning(
                f"Unauthorized ticket creation attempt for message {message_id} with reviewer '{approver_id}'",
                extra={"event": "UNAUTHORIZED_CONFIRM_ATTEMPT", "reviewer": approver_id, "messageId": message_id}
            )
            return render_confirmation_html(
                title="No Authority to Confirm",
                status_type="error",
                heading="Authority Check Failed",
                message="You have no authority to confirm Jira ticket creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"

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
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create ticket"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title="Jira Ticket Created Successfully",
            status_type="success",
            heading=f"Approved by {approver}",
            message=f"Ticket {key} has been created and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )
    return res


@router.api_route("/api/jira/decline-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_approval_get(
    message_id: str,
    request: Request,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM/TL Decline endpoint for Teams card action links (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    if reviewer:
        member = resolve_and_verify_approver(reviewer)
        if not member:
            return render_confirmation_html(
                title="No Authority to Reject",
                status_type="error",
                heading="Authority Check Failed",
                message="You have no authority to reject Jira ticket creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    else:
        approver = get_approver_name_for_message(row=row)

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason)
    if not res.get("success"):
        return render_confirmation_html(
            title="Reject Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", "Failed to reject ticket"),
            status_code=400,
        )

    return render_confirmation_html(
        title="Ticket Creation Rejected",
        status_type="declined",
        heading=f"Rejected by {approver}",
        message=f"Ticket creation was rejected by {approver}. No tickets were created in Jira, and notification has been posted to Teams. You can close this window now.",
        details={"Status": "Rejected", "Rejected By": approver},
    )


@router.post("/api/jira/decline-approval/{message_id}")
async def decline_approval_post(message_id: str, request: Request):
    """PM/TL Decline endpoint (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    approver_id = None
    reason = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
            reason = body.get("reason")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
            reason = form.get("reason")
        except Exception:
            pass

    if not approver_id and not reason:
        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Message not found")
        approver = get_approver_name_for_message(row=row)
    else:
        member = resolve_and_verify_approver(approver_id)
        if not member:
            return render_confirmation_html(
                title="No Authority to Reject",
                status_type="error",
                heading="Authority Check Failed",
                message="You have no authority to reject Jira ticket creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason)
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Reject Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", "Failed to reject ticket"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline ticket"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        return render_confirmation_html(
            title="Ticket Creation Rejected",
            status_type="declined",
            heading=f"Rejected by {approver}",
            message=f"Ticket creation was rejected by {approver}. No tickets were created in Jira, and notification has been posted to Teams.",
            details={"Status": "Rejected", "Rejected By": approver},
        )
    return res


@router.api_route("/api/jira/confirm-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_all_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await confirm_approval_get(message_id, request, assignee=assignee, reviewer=reviewer, auto=auto)


@router.post("/api/jira/confirm-issue/{message_id}")
async def confirm_issue_all_post(message_id: str, request: Request):
    """Programmatic PM Approval for all issues in a message (POST)."""
    return await confirm_approval_post(message_id, request)


@router.api_route("/api/jira/decline-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_issue_all_get(
    message_id: str,
    request: Request,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM Decline for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await decline_approval_get(message_id, request, reviewer=reviewer, reason=reason)


@router.post("/api/jira/decline-issue/{message_id}")
async def decline_issue_all_post(message_id: str, request: Request):
    """Programmatic PM Decline for all issues in a message (POST)."""
    return await decline_approval_post(message_id, request)


@router.api_route("/api/jira/confirm-issue/{message_id}/{issue_idx}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_get(
    message_id: str,
    issue_idx: int,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval for a specific single issue in a multi-issue triage card (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation
    import json

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    ai_ticket = {}
    if row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            pass

    issues = ai_ticket.get("issues", [])
    if not issues or not isinstance(issues, list):
        issues = [ai_ticket]

    if not (0 <= issue_idx < len(issues)):
        return render_confirmation_html(
            title="Invalid Issue",
            status_type="error",
            heading="Error",
            message=f"Issue #{issue_idx + 1} was not found in this message.",
            status_code=400,
        )

    target_issue = issues[issue_idx]
    if target_issue.get("status") == "APPROVED" or target_issue.get("jira_key"):
        active_key = target_issue.get("jira_key") or row["jira_issue_key"]
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Already Created",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Active in Jira",
            message=f"Ticket {active_key} has already been created for Issue #{issue_idx + 1}. You can close this window now.",
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

    if reviewer:
        member = resolve_and_verify_approver(reviewer)
        if not member:
            return render_confirmation_html(
                title="No Authority to Confirm",
                status_type="error",
                heading="Authority Check Failed",
                message=f"You have no authority to confirm Issue #{issue_idx + 1} creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    else:
        approver = get_approver_name_for_message(row=row)

    target_assignee = assignee or target_issue.get("suggested_assignee")

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, issue_idx=issue_idx, assignee_override=target_assignee
    )
    if not res.get("success"):
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Creation Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", f"Failed to create Issue #{issue_idx + 1}"),
            status_code=400,
        )

    key = res.get("key", "Created")
    url = res.get("url", "#")
    display_dev = target_assignee or "Assigned Developer"
    already = res.get("already_existed", False)

    return render_confirmation_html(
        title=f"Issue #{issue_idx + 1} {'Already Created' if already else 'Created Successfully'}",
        status_type="success",
        heading=f"Issue #{issue_idx + 1} Approved by {approver}",
        message=f"Ticket {key} {'is already active in Jira' if already else f'has been created in Jira and assigned to {display_dev}. Confirmation posted to Teams.'} You can close this window now.",
        details={"Jira Ticket": key, "Status": "Active in Jira", "Assignee": display_dev, "Approved By": approver},
        actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
    )


@router.post("/api/jira/confirm-issue/{message_id}/{issue_idx}")
async def confirm_issue_post(
    message_id: str,
    issue_idx: int,
    request: Request,
):
    """Role-verified PM/TL Approval for a specific single issue (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation

    assignee = None
    approver_id = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
        except Exception:
            pass

    # Strictly verify that reviewer is an authorized management member (TL, PM, HM)
    member = resolve_and_verify_approver(approver_id)
    if not member:
        logger.warning(
            f"Unauthorized confirmation attempt for issue #{issue_idx + 1} with reviewer '{approver_id}'",
            extra={"event": "UNAUTHORIZED_CONFIRM_ATTEMPT", "reviewer": approver_id, "messageId": message_id, "issueIdx": issue_idx}
        )
        return render_confirmation_html(
            title="No Authority to Confirm",
            status_type="error",
            heading="Authority Check Failed",
            message=f"You have no authority to confirm Issue #{issue_idx + 1} creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
            status_code=403,
        )

    role = (member.get("role") or "PM").upper().strip()
    approver = f"{role} {member.get('display_name')}"

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
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create Jira issue"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Created Successfully",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Approved by {approver}",
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
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM Decline for a specific single issue in a multi-issue triage card (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    if reviewer:
        member = resolve_and_verify_approver(reviewer)
        if not member:
            return render_confirmation_html(
                title="No Authority to Reject",
                status_type="error",
                heading="Authority Check Failed",
                message=f"You have no authority to reject Issue #{issue_idx + 1} creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    else:
        approver = get_approver_name_for_message(row=row)

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, issue_idx=issue_idx, reason=reason)
    if not res.get("success"):
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Reject Failed",
            status_type="error",
            heading="Cannot Reject",
            message=res.get("error", "Failed to reject issue"),
            status_code=400,
        )

    return render_confirmation_html(
        title=f"Issue #{issue_idx + 1} Rejected",
        status_type="declined",
        heading=f"Issue #{issue_idx + 1} Rejected by {approver}",
        message=f"Issue #{issue_idx + 1} was rejected by {approver}. No Jira ticket was created for this issue. You can close this window now.",
        details={"Status": f"Issue #{issue_idx + 1} Rejected", "Rejected By": approver},
    )


@router.post("/api/jira/decline-issue/{message_id}/{issue_idx}")
async def decline_issue_post(message_id: str, issue_idx: int, request: Request):
    """PM/TL Decline for a specific single issue (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    approver_id = None
    reason = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
            reason = body.get("reason")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
            reason = form.get("reason")
        except Exception:
            pass

    member = resolve_and_verify_approver(approver_id)
    if not member:
        return render_confirmation_html(
            title="No Authority to Reject",
            status_type="error",
            heading="Authority Check Failed",
            message=f"You have no authority to reject Issue #{issue_idx + 1} creation. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
            status_code=403,
        )

    role = (member.get("role") or "PM").upper().strip()
    approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason, issue_idx=issue_idx)
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Cannot Reject",
                message=res.get("error", "Failed to reject issue"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline Jira issue"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Rejected",
            status_type="declined",
            heading=f"Issue #{issue_idx + 1} Rejected by {approver}",
            message=f"Issue #{issue_idx + 1} was rejected by {approver}. No Jira ticket was created for this issue. You can close this window now.",
            details={"Status": f"Issue #{issue_idx + 1} Rejected", "Rejected By": approver},
        )
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

