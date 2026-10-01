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
)
from src.graph_client import renew_subscription, delete_subscription
from src.tunnel import get_active_tunnel_url

router = APIRouter()
start_timestamp = time.time()


class SimulateMessageRequest(BaseModel):
    role: str = "CLIENT"  # CLIENT, PM, DEVELOPER
    text: str
    sender_name: Optional[str] = None


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

    return {
        "status": "online",
        "service": "Teams -> Jira Automation Engine",
        "uptime": round(time.time() - start_timestamp, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tunnel": {
            "active": bool(tunnel_url),
            "url": tunnel_url,
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
            "phase4": {"name": "Jira Ticket Creation", "status": "pending_keys"},
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
    """Manual trigger to renew subscription from dashboard."""
    sub_info = get_active_subscription_info()
    if not sub_info.get("active"):
        raise HTTPException(status_code=400, detail="No active subscription found to renew")

    sub_id = sub_info["id"]
    try:
        result = renew_subscription(sub_id, expiration_minutes=60)
        return {"success": True, "subscription": result}
    except Exception as err:
        logger.error(f"Failed manual renewal: {err}")
        raise HTTPException(status_code=500, detail=str(err))


@router.post("/api/subscription/create")
def create_sub():
    """Manual trigger to create or recreate subscription from dashboard."""
    try:
        result = ensure_subscription_online()
        return {"success": True, "result": result}
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
    role = req.role.upper()
    role_map = {
        "CLIENT": (config.roles.client, req.sender_name or "Dhruv dobariya"),
        "PM": (config.roles.pm, req.sender_name or "Santosh Yadav"),
        "DEVELOPER": (config.roles.developer, req.sender_name or "Musaib Khan"),
    }

    user_id, display_name = role_map.get(role, ("sim-user-" + str(uuid.uuid4())[:6], req.sender_name or "Test User"))
    sim_id = "sim-" + str(int(time.time() * 1000))

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
        "attachments": [],
    }

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
