import time
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src.database import get_db
from src.logger import logger
from src.config import config
from src.services.config_manager import (
    get_system_config,
    update_env_file,
    test_jira_credentials,
    test_gemini_credentials,
    test_teams_webhook_payload,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# Models for Admin requests
class MemberCreateRequest(BaseModel):
    display_name: str
    role: str = "DEVELOPER"  # CLIENT, PM, DEVELOPER, ADMIN
    email: Optional[str] = None
    user_id: Optional[str] = None
    specialty: Optional[str] = None
    can_approve: bool = False


class MemberUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    user_id: Optional[str] = None
    specialty: Optional[str] = None
    can_approve: Optional[bool] = None
    is_active: Optional[bool] = None


class ChannelCreateRequest(BaseModel):
    name: str
    type: str = "chat"  # chat, channel
    chat_id: Optional[str] = None
    team_id: Optional[str] = None
    channel_id: Optional[str] = None
    webhook_url: Optional[str] = None
    usage: str = "CLIENT_SUPPORT"  # CLIENT_SUPPORT, PM_APPROVALS, DEV_ALERTS, GENERAL


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    chat_id: Optional[str] = None
    team_id: Optional[str] = None
    channel_id: Optional[str] = None
    webhook_url: Optional[str] = None
    usage: Optional[str] = None
    is_active: Optional[bool] = None


class ConfigUpdateRequest(BaseModel):
    jira_base_url: Optional[str] = None
    jira_email: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: Optional[str] = None
    jira_default_issue_type: Optional[str] = None

    teams_chat_id: Optional[str] = None
    teams_team_id: Optional[str] = None
    teams_channel_id: Optional[str] = None
    teams_webhook_url: Optional[str] = None
    allow_self_approval: Optional[bool] = None

    gemini_api_key_1: Optional[str] = None
    gemini_api_key_2: Optional[str] = None
    gemini_api_key_3: Optional[str] = None
    gemini_model: Optional[str] = None

    webhook_public_url: Optional[str] = None
    performed_by: Optional[str] = "Admin"


class TestJiraRequest(BaseModel):
    base_url: str
    email: str
    api_token: str
    project_key: str


class TestGeminiRequest(BaseModel):
    api_key: str
    model: Optional[str] = "gemini-3.5-flash-lite"


class TestTeamsRequest(BaseModel):
    webhook_url: str


@router.get("/overview")
def get_admin_overview():
    """Return dashboard summary metrics for the management overview."""
    db = get_db()
    
    # Team members breakdown
    members = db.execute("SELECT * FROM team_members").fetchall()
    roles_summary = {"CLIENT": 0, "PM": 0, "DEVELOPER": 0, "ADMIN": 0}
    for m in members:
        if m["is_active"]:
            r = (m["role"] or "DEVELOPER").upper()
            roles_summary[r] = roles_summary.get(r, 0) + 1

    # Channels count
    channels = db.execute("SELECT * FROM monitored_channels").fetchall()
    active_channels = sum(1 for c in channels if c["is_active"])

    # Recent Audit Log count
    log_count = db.execute("SELECT COUNT(*) FROM admin_audit_log").fetchone()[0]

    return {
        "success": True,
        "team": {
            "total": len(members),
            "active": sum(1 for m in members if m["is_active"]),
            "breakdown": roles_summary,
        },
        "channels": {
            "total": len(channels),
            "active": active_channels,
        },
        "jira": {
            "isConfigured": config.jira.is_configured,
            "projectKey": config.jira.project_key,
            "baseUrl": config.jira.base_url,
        },
        "gemini": {
            "model": config.gemini.model,
            "keysCount": len(config.gemini.api_keys or []),
        },
        "auditLogCount": log_count,
    }


# =========================================================================
# Team Members Endpoints
# =========================================================================

@router.get("/members")
def list_team_members():
    """List all team members and their roles."""
    db = get_db()
    rows = db.execute("SELECT * FROM team_members ORDER BY id ASC").fetchall()
    return {"success": True, "members": [dict(r) for r in rows]}


@router.post("/members")
def create_team_member(req: MemberCreateRequest):
    """Add a new member with designated role and specialty."""
    if not req.display_name or not req.display_name.strip():
        raise HTTPException(status_code=400, detail="Display name is required.")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO team_members (user_id, display_name, email, role, specialty, can_approve, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            req.user_id.strip() if req.user_id else "",
            req.display_name.strip(),
            req.email.strip() if req.email else "",
            req.role.upper().strip(),
            req.specialty.strip() if req.specialty else "",
            1 if req.can_approve else 0,
        ),
    )
    new_id = cursor.lastrowid

    # Audit log
    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("MEMBERS", "MEMBER_CREATED", f"Added member '{req.display_name}' with role '{req.role.upper()}'"),
    )

    row = db.execute("SELECT * FROM team_members WHERE id = ?", (new_id,)).fetchone()
    return {"success": True, "member": dict(row)}


@router.put("/members/{member_id}")
def update_team_member(member_id: int, req: MemberUpdateRequest):
    """Update role, specialty, approval permission, or active status of a member."""
    db = get_db()
    row = db.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Member not found.")

    current = dict(row)
    new_name = req.display_name if req.display_name is not None else current["display_name"]
    new_role = req.role.upper() if req.role is not None else current["role"]
    new_email = req.email if req.email is not None else current["email"]
    new_user_id = req.user_id if req.user_id is not None else current["user_id"]
    new_specialty = req.specialty if req.specialty is not None else current["specialty"]
    new_can_approve = (1 if req.can_approve else 0) if req.can_approve is not None else current["can_approve"]
    new_is_active = (1 if req.is_active else 0) if req.is_active is not None else current["is_active"]

    db.execute(
        """
        UPDATE team_members
        SET display_name = ?, role = ?, email = ?, user_id = ?, specialty = ?, can_approve = ?, is_active = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (new_name, new_role, new_email, new_user_id, new_specialty, new_can_approve, new_is_active, member_id),
    )

    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("MEMBERS", "MEMBER_UPDATED", f"Updated member '{new_name}': role={new_role}, can_approve={new_can_approve}, active={new_is_active}"),
    )

    updated = db.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()
    return {"success": True, "member": dict(updated)}


@router.delete("/members/{member_id}")
def delete_team_member(member_id: int):
    """Delete a team member."""
    db = get_db()
    row = db.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Member not found.")

    name = row["display_name"]
    db.execute("DELETE FROM team_members WHERE id = ?", (member_id,))
    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("MEMBERS", "MEMBER_DELETED", f"Removed member '{name}' (ID {member_id})"),
    )
    return {"success": True, "message": f"Member '{name}' removed successfully."}


# =========================================================================
# Monitored Channels / Chats Endpoints
# =========================================================================

@router.get("/channels")
def list_monitored_channels():
    """List all monitored Teams channels and chats with their usages."""
    db = get_db()
    rows = db.execute("SELECT * FROM monitored_channels ORDER BY id ASC").fetchall()
    return {"success": True, "channels": [dict(r) for r in rows]}


@router.post("/channels")
def create_monitored_channel(req: ChannelCreateRequest):
    """Add a new Teams channel or chat to monitor."""
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Channel/Chat name is required.")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO monitored_channels (name, type, chat_id, team_id, channel_id, webhook_url, usage, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            req.name.strip(),
            req.type.lower().strip(),
            req.chat_id.strip() if req.chat_id else "",
            req.team_id.strip() if req.team_id else "",
            req.channel_id.strip() if req.channel_id else "",
            req.webhook_url.strip() if req.webhook_url else "",
            req.usage.upper().strip(),
        ),
    )
    new_id = cursor.lastrowid

    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("CHANNELS", "CHANNEL_ADDED", f"Added monitored {req.type} '{req.name}' for usage '{req.usage.upper()}'"),
    )

    row = db.execute("SELECT * FROM monitored_channels WHERE id = ?", (new_id,)).fetchone()
    return {"success": True, "channel": dict(row)}


@router.put("/channels/{channel_id}")
def update_monitored_channel(channel_id: int, req: ChannelUpdateRequest):
    """Update channel/chat settings, usage type, webhook, or status."""
    db = get_db()
    row = db.execute("SELECT * FROM monitored_channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found.")

    current = dict(row)
    new_name = req.name if req.name is not None else current["name"]
    new_type = req.type if req.type is not None else current["type"]
    new_chat_id = req.chat_id if req.chat_id is not None else current["chat_id"]
    new_team_id = req.team_id if req.team_id is not None else current["team_id"]
    new_channel_id = req.channel_id if req.channel_id is not None else current["channel_id"]
    new_webhook = req.webhook_url if req.webhook_url is not None else current["webhook_url"]
    new_usage = req.usage.upper() if req.usage is not None else current["usage"]
    new_is_active = (1 if req.is_active else 0) if req.is_active is not None else current["is_active"]

    db.execute(
        """
        UPDATE monitored_channels
        SET name = ?, type = ?, chat_id = ?, team_id = ?, channel_id = ?, webhook_url = ?, usage = ?, is_active = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (new_name, new_type, new_chat_id, new_team_id, new_channel_id, new_webhook, new_usage, new_is_active, channel_id),
    )

    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("CHANNELS", "CHANNEL_UPDATED", f"Updated channel '{new_name}': usage={new_usage}, active={new_is_active}"),
    )

    updated = db.execute("SELECT * FROM monitored_channels WHERE id = ?", (channel_id,)).fetchone()
    return {"success": True, "channel": dict(updated)}


@router.delete("/channels/{channel_id}")
def delete_monitored_channel(channel_id: int):
    """Remove a channel from monitored list."""
    db = get_db()
    row = db.execute("SELECT * FROM monitored_channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Channel not found.")

    name = row["name"]
    db.execute("DELETE FROM monitored_channels WHERE id = ?", (channel_id,))
    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("CHANNELS", "CHANNEL_REMOVED", f"Removed monitored channel '{name}' (ID {channel_id})"),
    )
    return {"success": True, "message": f"Channel '{name}' removed successfully."}


# =========================================================================
# System Config & Secrets Endpoints
# =========================================================================

@router.get("/config")
def read_system_config(raw: bool = Query(False, description="Whether to include unmasked secrets")):
    """Read current system configurations with secrets optionally masked for safety."""
    return {"success": True, "config": get_system_config(include_raw_secrets=raw)}


def is_secret_masked(val: Optional[str]) -> bool:
    if not val:
        return False
    val_str = str(val).strip()
    return "•" in val_str or "…" in val_str or val_str.startswith("••")


@router.post("/config")
def save_system_config(req: ConfigUpdateRequest):
    """Save updated system configuration to .env and apply changes live."""
    updates: Dict[str, str] = {}

    if req.jira_base_url is not None:
        updates["JIRA_BASE_URL"] = req.jira_base_url.rstrip("/")
    if req.jira_email is not None:
        updates["JIRA_EMAIL"] = req.jira_email.strip()
    if req.jira_api_token is not None and req.jira_api_token.strip() and not is_secret_masked(req.jira_api_token):
        updates["JIRA_API_TOKEN"] = req.jira_api_token.strip()
    if req.jira_project_key is not None:
        updates["JIRA_PROJECT_KEY"] = req.jira_project_key.upper().strip()
    if req.jira_default_issue_type is not None:
        updates["JIRA_DEFAULT_ISSUE_TYPE"] = req.jira_default_issue_type.strip()

    if req.teams_chat_id is not None:
        updates["TEAMS_CHAT_ID"] = req.teams_chat_id.strip()
    if req.teams_team_id is not None:
        updates["TEAMS_TEAM_ID"] = req.teams_team_id.strip()
    if req.teams_channel_id is not None:
        updates["TEAMS_CHANNEL_ID"] = req.teams_channel_id.strip()
    if req.teams_webhook_url is not None:
        updates["TEAMS_WEBHOOK_URL"] = req.teams_webhook_url.strip()
    if req.allow_self_approval is not None:
        updates["ALLOW_SELF_APPROVAL"] = "true" if req.allow_self_approval else "false"

    if req.gemini_api_key_1 is not None and not is_secret_masked(req.gemini_api_key_1):
        updates["GEMINI_API_KEY_1"] = req.gemini_api_key_1.strip()
        updates["GEMINI_API_KEY"] = req.gemini_api_key_1.strip()
    if req.gemini_api_key_2 is not None and not is_secret_masked(req.gemini_api_key_2):
        updates["GEMINI_API_KEY_2"] = req.gemini_api_key_2.strip()
    if req.gemini_api_key_3 is not None and not is_secret_masked(req.gemini_api_key_3):
        updates["GEMINI_API_KEY_3"] = req.gemini_api_key_3.strip()
    if req.gemini_model is not None:
        updates["GEMINI_MODEL"] = req.gemini_model.strip()

    if req.webhook_public_url is not None:
        updates["WEBHOOK_PUBLIC_URL"] = req.webhook_public_url.rstrip("/")

    if not updates:
        return {"success": True, "message": "No configuration changes detected."}

    res = update_env_file(updates, performed_by=req.performed_by or "Admin")
    return {"success": True, "result": res, "config": get_system_config(include_raw_secrets=False)}


# =========================================================================
# Live Connection Testers (Instant Feedback)
# =========================================================================

@router.post("/test/jira")
async def test_jira(req: TestJiraRequest):
    """Test connection to Jira Cloud with given credentials."""
    # If API token is masked (e.g. user didn't modify it), use the active token from config
    token = req.api_token
    if not token or is_secret_masked(token):
        token = config.jira.api_token

    res = await test_jira_credentials(
        base_url=req.base_url,
        email=req.email,
        api_token=token,
        project_key=req.project_key,
    )
    return res


@router.post("/test/gemini")
async def test_gemini(req: TestGeminiRequest):
    """Test Gemini API key connectivity and latency."""
    key = req.api_key
    if not key or is_secret_masked(key):
        key = config.gemini.api_key

    res = await test_gemini_credentials(api_key=key, model=req.model or config.gemini.model)
    return res


@router.post("/test/teams")
async def test_teams(req: TestTeamsRequest):
    """Send test Adaptive Card to a Teams Webhook URL."""
    url = req.webhook_url
    if not url:
        url = config.teams.webhook_url

    res = await test_teams_webhook_payload(webhook_url=url)
    return res


class TestEmailRequest(BaseModel):
    to_email: Optional[str] = None
    subject: Optional[str] = None


@router.post("/test/email")
async def test_email(req: TestEmailRequest):
    """Test Outlook email delivery to PM or custom address."""
    from src.services.email_service import send_pm_followup_email
    from src.services.reminder_service import get_active_pm
    pm = get_active_pm()
    target_email = req.to_email or pm.get("email") or "santosh.yadav@kombee.com"
    target_name = pm.get("name") or "Santosh Yadav"

    res = await send_pm_followup_email(
        pm_email=target_email,
        pm_name=target_name,
        reporter_name="Client Test User",
        elapsed_minutes=15,
        issues=[{
            "summary": "Sample client issue: Search dropdown filter unresponsive on product catalog",
            "issue_type": config.jira.default_issue_type,
            "priority": "High",
            "affected_module": "Product Catalog",
            "suggested_assignee": "Frontend & UI Lead (Musaib Khan)",
            "observed_behavior": "Clicking the category filter results in a frozen dropdown and console error 500."
        }],
        raw_message="#issue The product catalog search dropdown is unresponsive when selecting multiple categories.",
        message_id="test_email_verification",
        created_at_str="2026-10-06 09:30:00",
    )
    return {
        "success": res.get("success", False),
        "target_email": target_email,
        "details": res,
    }


@router.get("/audit-log")
def get_audit_log(limit: int = 50):
    """Get recent admin audit actions."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM admin_audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {"success": True, "logs": [dict(r) for r in rows]}
