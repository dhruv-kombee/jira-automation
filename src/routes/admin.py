import csv
import io
import re
import time
from typing import Optional, List, Dict, Any
import httpx
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Response
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


class MemberImportRequest(BaseModel):
    url: Optional[str] = None
    csv_text: Optional[str] = None
    strategy: str = "upsert"  # upsert or replace
    members: Optional[List[Dict[str, Any]]] = None


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
# Team Members Spreadsheet & Link Import Endpoints
# =========================================================================

async def fetch_sheet_csv_from_url(url: str) -> str:
    """Fetch CSV content from a Google Sheets URL or public web CSV link."""
    clean_url = url.strip()
    gsheet_match = re.search(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)", clean_url)
    if gsheet_match:
        sheet_id = gsheet_match.group(1)
        gid_match = re.search(r"[#&?]gid=([0-9]+)", clean_url)
        gid_part = f"&gid={gid_match.group(1)}" if gid_match else ""
        clean_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv{gid_part}"

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            res = await client.get(clean_url)
            if res.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not download sheet (HTTP {res.status_code}). Ensure link is shared as 'Anyone with the link can view'."
                )
            return res.text
    except HTTPException:
        raise
    except Exception as fetch_err:
        raise HTTPException(status_code=400, detail=f"Failed to fetch sheet from link: {fetch_err}")


def parse_rows_from_content(
    file_bytes: Optional[bytes] = None,
    text_content: Optional[str] = None,
    filename: str = ""
) -> List[Dict[str, Any]]:
    """Parse member rows from CSV text, uploaded CSV file, or Excel .xlsx workbook."""
    raw_rows: List[Dict[str, Any]] = []

    # Case 1: Excel workbook (.xlsx / .xls)
    if file_bytes and (filename.endswith(".xlsx") or filename.endswith(".xls") or file_bytes[:4] == b"PK\x03\x04"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            ws = wb.active
            rows_iter = list(ws.iter_rows(values_only=True))
            if not rows_iter:
                return []
            header = [str(c or "").strip() for c in rows_iter[0]]
            for r in rows_iter[1:]:
                if not any(r):
                    continue
                row_dict = {header[i]: (str(val).strip() if val is not None else "") for i, val in enumerate(r) if i < len(header)}
                raw_rows.append(row_dict)
        except Exception as xl_err:
            raise HTTPException(status_code=400, detail=f"Failed to read Excel workbook: {xl_err}")

    # Case 2: CSV text or file
    else:
        text = text_content
        if not text and file_bytes:
            for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
                try:
                    text = file_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        if not text:
            return []

        delimiter = ","
        first_line = text.strip().split("\n")[0] if text.strip() else ""
        if "\t" in first_line:
            delimiter = "\t"
        elif ";" in first_line and "," not in first_line:
            delimiter = ";"

        reader = csv.DictReader(io.StringIO(text.strip()), delimiter=delimiter)
        for r in reader:
            if not any(r.values()):
                continue
            raw_rows.append({str(k or "").strip(): str(v or "").strip() for k, v in r.items()})

    # Normalize rows into standard member structures
    normalized_members: List[Dict[str, Any]] = []
    for r in raw_rows:
        name = ""
        email = ""
        role = "DEVELOPER"
        specialty = ""
        user_id = ""
        can_approve = None

        for k, v in r.items():
            k_clean = k.lower().replace(" ", "_").replace("-", "_")
            val_clean = str(v).strip()
            if not val_clean:
                continue

            if k_clean in ("name", "display_name", "member_name", "full_name", "member", "user"):
                name = val_clean
            elif k_clean in ("email", "mail", "email_address", "e_mail"):
                email = val_clean
            elif k_clean in ("role", "roles", "assigned_role", "designation", "type"):
                r_upper = val_clean.upper()
                if "CLIENT" in r_upper or "CUSTOMER" in r_upper:
                    role = "CLIENT"
                elif "PM" in r_upper or "PROJECT_MANAGER" in r_upper or "SCRUM" in r_upper:
                    role = "PM"
                elif "DEV" in r_upper or "ENGINEER" in r_upper or "TECH" in r_upper:
                    role = "DEVELOPER"
                else:
                    role = r_upper
            elif k_clean in ("specialty", "specialisation", "focus", "focus_area", "skills", "skill", "area"):
                specialty = val_clean
            elif k_clean in ("user_id", "teams_user_id", "graph_id", "azure_id", "teams_id", "id"):
                user_id = val_clean
            elif k_clean in ("can_approve", "approver", "approve"):
                can_approve = val_clean.lower() in ("1", "true", "yes", "y", "t")

        if not name:
            continue

        if can_approve is None:
            can_approve = (role in ("CLIENT", "PM"))

        normalized_members.append({
            "display_name": name,
            "email": email,
            "role": role,
            "specialty": specialty or (
                "Client Product Owner" if role == "CLIENT" else
                "Project Manager / Scrum Master" if role == "PM" else
                "Software Engineer"
            ),
            "user_id": user_id,
            "can_approve": bool(can_approve),
        })

    return normalized_members


def execute_members_upsert(members: List[Dict[str, Any]], strategy: str = "upsert") -> Dict[str, Any]:
    """Execute insertion or update into SQLite team_members."""
    db = get_db()
    cursor = db.cursor()
    imported_count = 0
    updated_count = 0

    if strategy == "replace":
        cursor.execute("DELETE FROM team_members")

    for m in members:
        disp_name = (m.get("display_name") or "").strip()
        if not disp_name:
            continue
        email = (m.get("email") or "").strip()
        role = (m.get("role") or "DEVELOPER").upper().strip()
        specialty = (m.get("specialty") or "").strip()
        user_id = (m.get("user_id") or "").strip()
        can_approve = 1 if m.get("can_approve") else 0

        existing = None
        if email:
            existing = cursor.execute("SELECT id FROM team_members WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()
        if not existing:
            existing = cursor.execute("SELECT id FROM team_members WHERE LOWER(display_name) = LOWER(?)", (disp_name,)).fetchone()

        if existing and strategy != "replace":
            m_id = existing["id"]
            cursor.execute(
                """
                UPDATE team_members
                SET display_name = ?, email = COALESCE(NULLIF(?, ''), email),
                    role = ?, specialty = COALESCE(NULLIF(?, ''), specialty),
                    user_id = COALESCE(NULLIF(?, ''), user_id),
                    can_approve = ?, is_active = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (disp_name, email, role, specialty, user_id, can_approve, m_id),
            )
            updated_count += 1
        else:
            cursor.execute(
                """
                INSERT INTO team_members (user_id, display_name, email, role, specialty, can_approve, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (user_id, disp_name, email, role, specialty, can_approve),
            )
            imported_count += 1

    action_label = "REPLACED_ALL" if strategy == "replace" else "IMPORTED_OR_UPDATED"
    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("MEMBERS", f"MEMBERS_{action_label}", f"Imported {imported_count} new, updated {updated_count} members via spreadsheet import"),
    )

    all_rows = db.execute("SELECT * FROM team_members ORDER BY id ASC").fetchall()
    return {
        "success": True,
        "imported_count": imported_count,
        "updated_count": updated_count,
        "total": len(all_rows),
        "members": [dict(r) for r in all_rows],
    }


@router.get("/members/template")
def download_members_template():
    """Download standard CSV template for team members import."""
    csv_content = (
        "Name,Email,Role,Specialty,Teams_User_ID,Can_Approve\n"
        "Dhruv Dobariya,dhruv.d.kombee@gmail.com,CLIENT,Client Product Owner,35e03956-1723-469c-b561-90f03fc566ed,1\n"
        "Hemil Ghori,hemil.ghori@kombee.com,PM,Project Manager / Scrum Master,83b10217-f4c0-4f87-97f9-d95a33ddaaa0,1\n"
        "Santosh Yadav,santosh.yadav@kombee.com,DEVELOPER,Backend & API Lead,d7bc3c28-33d9-4973-816e-445d51556b8b,0\n"
        "Musaib Khan,musaib.khan@kombee.com,DEVELOPER,Frontend & UI Lead,c5a63f53-cc7a-4c05-ac9a-77e6991bc974,0\n"
    )
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=team_members_template.csv"},
    )


@router.post("/members/parse-sheet")
async def parse_members_sheet(req: MemberImportRequest):
    """Parse sheet URL or raw CSV text and return preview of rows."""
    text_content = req.csv_text
    if req.url and req.url.strip():
        text_content = await fetch_sheet_csv_from_url(req.url.strip())

    if not text_content:
        raise HTTPException(status_code=400, detail="Provide a Google Sheets URL or raw CSV text.")

    members = parse_rows_from_content(text_content=text_content)
    return {"success": True, "count": len(members), "preview": members}


@router.post("/members/parse-file")
async def parse_members_file(file: UploadFile = File(...)):
    """Parse CSV or Excel (.xlsx) file and return preview rows without saving."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    members = parse_rows_from_content(file_bytes=content, filename=file.filename or "")
    return {"success": True, "count": len(members), "preview": members}



@router.post("/members/import-json")
async def import_members_json(req: MemberImportRequest):
    """Import team members from parsed list, Google Sheets URL, or raw CSV text."""
    members = req.members
    if not members:
        text_content = req.csv_text
        if req.url and req.url.strip():
            text_content = await fetch_sheet_csv_from_url(req.url.strip())
        if not text_content:
            raise HTTPException(status_code=400, detail="No members data, URL, or CSV text provided.")
        members = parse_rows_from_content(text_content=text_content)

    if not members:
        raise HTTPException(status_code=400, detail="No valid member rows found in the sheet.")

    return execute_members_upsert(members, strategy=req.strategy or "upsert")


@router.post("/members/import-file")
async def import_members_file(file: UploadFile = File(...), strategy: str = Form("upsert")):
    """Upload CSV or Excel (.xlsx) file directly to import members."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    members = parse_rows_from_content(file_bytes=content, filename=file.filename or "")
    if not members:
        raise HTTPException(status_code=400, detail="No valid member rows found in the uploaded file.")

    return execute_members_upsert(members, strategy=strategy)


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
