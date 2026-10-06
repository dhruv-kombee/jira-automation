"""Teams Roster & OneDrive Excel (Member.xlsx) Sync Service.

Manages automatic discovery of members in Microsoft Teams group chats and channels,
and maintains two-way synchronization with the shared Excel sheet (Member.xlsx):
  1. Auto-registers new members when discovered in Teams chat/channel roster or when posting messages.
  2. Generates & maintains a formatted Member.xlsx workbook containing all member details and project info.
  3. Supports 1-click sync directly with the OneDrive shared sheet link.
"""
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.config import config
from src.database import get_db
from src.graph_client import get_access_token
from src.logger import logger

MEMBER_FILE_PATH = Path("data/Member.xlsx")
DEFAULT_ONEDRIVE_URL = os.getenv(
    "MEMBER_SHEET_URL",
    "https://1drv.ms/x/c/329A45768254A220/IQCs8zX7occ_T7QD0sO3qzfhAf0ZXEgkcl9IzywZD9Z1Ru0?e=xb5pxk",
)


def get_onedrive_download_url(share_url: str) -> str:
    """Transform a OneDrive sharing URL into a direct binary download URL."""
    clean = share_url.strip()
    if "?download=1" in clean or "&download=1" in clean:
        return clean
    if "?" in clean:
        return f"{clean}&download=1"
    return f"{clean}?download=1"


def download_onedrive_workbook(share_url: Optional[str] = None) -> Optional[bytes]:
    """Download the raw Excel bytes from a OneDrive sharing link."""
    url = share_url or DEFAULT_ONEDRIVE_URL
    if not url:
        return None

    download_url = get_onedrive_download_url(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            res = client.get(download_url)
            if res.status_code == 200 and len(res.content) > 500:
                return res.content
            logger.warning(f"OneDrive download returned HTTP {res.status_code}: {res.text[:150]}")
    except Exception as err:
        logger.error(f"Error downloading workbook from OneDrive: {err}")
    return None


def export_members_to_excel(output_path: Optional[Path] = None) -> bytes:
    """Generate a clean, styled Excel workbook containing all team members and project details."""
    target_path = output_path or MEMBER_FILE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    db = get_db()
    rows = db.execute(
        "SELECT * FROM team_members ORDER BY CASE role WHEN 'CLIENT' THEN 1 WHEN 'PM' THEN 2 WHEN 'ADMIN' THEN 3 ELSE 4 END, id ASC"
    ).fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Team Directory"

    # Set gridlines
    ws.views.sheetView[0].showGridLines = True

    # Title block
    ws.merge_cells("A1:I1")
    title_cell = ws.cell(row=1, column=1)
    title_cell.value = f"JIRA AUTOMATION HUB - TEAM ROSTER & ROLES DIRECTORY (Target: {config.jira.project_key or 'SCRUM'})"
    title_cell.font = Font(name="Segoe UI", size=13, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Subtitle with timestamp
    ws.merge_cells("A2:I2")
    sub_cell = ws.cell(row=2, column=1)
    sub_cell.value = f"Synced with Microsoft Teams Chat & Jira Cloud | Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    sub_cell.font = Font(name="Segoe UI", size=9, italic=True, color="94A3B8")
    sub_cell.fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # Headers
    headers = [
        "Full Name",
        "Email Address",
        "Assigned Role",
        "Specialty / Focus Area",
        "Teams Graph User ID",
        "Can Approve (1/0)",
        "Jira Project",
        "Status",
        "Last Updated",
    ]
    ws.append([])  # blank row 3
    ws.append(headers)  # row 4
    ws.row_dimensions[4].height = 24

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Segoe UI", size=10, bold=True, color="F8FAFC")
    thin_border = Border(
        left=Side(style="thin", color="334155"),
        right=Side(style="thin", color="334155"),
        top=Side(style="thin", color="334155"),
        bottom=Side(style="thin", color="334155"),
    )

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    # Color palettes for roles
    role_colors = {
        "CLIENT": ("EFF6FF", "1D4ED8"),     # blue-50, blue-700
        "PM": ("FEF3C7", "B45309"),         # amber-100, amber-700
        "DEVELOPER": ("ECFDF5", "047857"),  # green-50, green-700
        "ADMIN": ("F5F3FF", "6D28D9"),      # purple-50, purple-700
    }

    # Data Rows
    current_row = 5
    for r in rows:
        role = (r["role"] or "DEVELOPER").upper()
        bg_color, fg_color = role_colors.get(role, ("F8FAFC", "334155"))
        row_fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")

        data_values = [
            r["display_name"] or "",
            r["email"] or "",
            role,
            r["specialty"] or "General",
            r["user_id"] or "",
            "1" if r["can_approve"] else "0",
            config.jira.project_key or "SCRUM",
            "Active" if r["is_active"] else "Disabled",
            r["updated_at"] or r["created_at"] or datetime.now().strftime("%Y-%m-%d"),
        ]

        ws.append(data_values)
        ws.row_dimensions[current_row].height = 20

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=col_idx)
            cell.font = Font(name="Segoe UI", size=9.5)
            cell.border = thin_border

            # Alignments
            if col_idx in (1, 2, 4):
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif col_idx in (3, 6, 7, 8):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center")

            # Highlight Role Column
            if col_idx == 3:
                cell.fill = row_fill
                cell.font = Font(name="Segoe UI", size=9.5, bold=True, color=fg_color)

        current_row += 1

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val = str(cell.value or "")
            if "\n" in val:
                val = val.split("\n")[0]
            if len(val) > max_len and cell.row > 2:
                max_len = len(val)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Save to disk
    buffer = io.BytesIO()
    wb.save(buffer)
    xlsx_bytes = buffer.getvalue()

    try:
        with open(target_path, "wb") as f:
            f.write(xlsx_bytes)
        logger.info(f"Updated Excel file: {target_path} ({len(rows)} members)", extra={"event": "MEMBER_EXCEL_SAVED"})
    except Exception as save_err:
        logger.warning(f"Could not write {target_path} to disk: {save_err}")

    return xlsx_bytes


def auto_register_member(
    display_name: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    specialty: Optional[str] = None,
    can_approve: Optional[bool] = None,
) -> Dict[str, Any]:
    """Auto-register or update a discovered member into the system database."""
    name_clean = (display_name or "").strip()
    if not name_clean:
        return {"registered": False, "reason": "Empty display name"}

    user_id_clean = (user_id or "").strip()
    email_clean = (email or "").strip().lower()

    # Determine default role if not given
    assigned_role = role
    if not assigned_role:
        name_lower = name_clean.lower()
        if "hemil" in name_lower:
            assigned_role = "PM"
        elif "santosh" in name_lower:
            assigned_role = "PM"
        elif "dhruv" in name_lower:
            assigned_role = "CLIENT"
        elif "musaib" in name_lower or "musain" in name_lower:
            assigned_role = "DEVELOPER"
        else:
            assigned_role = "DEVELOPER"

    assigned_role = assigned_role.upper()

    # Default specialty
    if not specialty:
        if assigned_role == "CLIENT":
            specialty = "Client Product Owner"
        elif assigned_role == "PM":
            specialty = "Project Manager / Scrum Master"
        else:
            name_lower = name_clean.lower()
            if "musaib" in name_lower:
                specialty = "Frontend & UI Lead"
            elif "santosh" in name_lower:
                specialty = "Backend & API Lead"
            else:
                specialty = "Software Engineer"

    if can_approve is None:
        can_approve = assigned_role in ("PM", "CLIENT")

    db = get_db()
    cursor = db.cursor()

    # Check if exists by user_id, email, or display_name
    existing = None
    if user_id_clean:
        existing = cursor.execute(
            "SELECT * FROM team_members WHERE LOWER(user_id) = LOWER(?)", (user_id_clean,)
        ).fetchone()

    if not existing and email_clean:
        existing = cursor.execute(
            "SELECT * FROM team_members WHERE LOWER(email) = LOWER(?)", (email_clean,)
        ).fetchone()

    if not existing:
        existing = cursor.execute(
            "SELECT * FROM team_members WHERE LOWER(display_name) = LOWER(?)", (name_clean.lower(),)
        ).fetchone()

    if existing:
        # Update missing fields (fill empty email or user_id)
        m_id = existing["id"]
        cursor.execute(
            """
            UPDATE team_members
            SET user_id = COALESCE(NULLIF(?, ''), user_id),
                email = COALESCE(NULLIF(?, ''), email),
                display_name = COALESCE(NULLIF(?, ''), display_name),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (user_id_clean, email_clean, name_clean, m_id),
        )
        return {"registered": False, "updated": True, "id": m_id, "name": name_clean}

    # Insert new member
    cursor.execute(
        """
        INSERT INTO team_members (user_id, display_name, email, role, specialty, can_approve, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (user_id_clean, name_clean, email_clean, assigned_role, specialty, 1 if can_approve else 0),
    )
    new_id = cursor.lastrowid

    db.execute(
        "INSERT INTO admin_audit_log (category, action, details) VALUES (?, ?, ?)",
        ("MEMBERS", "MEMBER_AUTO_DISCOVERED", f"Discovered and registered '{name_clean}' as {assigned_role} ({email_clean})"),
    )

    logger.info(
        f"👥 Auto-registered new team member: {name_clean} as {assigned_role} ({email_clean})",
        extra={"event": "NEW_MEMBER_AUTO_DISCOVERED", "member_name": name_clean, "role": assigned_role},
    )

    # Re-export Excel
    export_members_to_excel()

    return {"registered": True, "id": new_id, "name": name_clean, "role": assigned_role}


def sync_teams_chat_roster(chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Poll Microsoft Graph API for all members in the Teams chat/channel and sync them."""
    target_chat_id = chat_id or config.teams.chat_id
    if not target_chat_id:
        return {"success": False, "error": "No Teams chat ID configured"}

    token = get_access_token()
    if not token:
        return {"success": False, "error": "Could not acquire Microsoft Graph token"}

    headers = {"Authorization": f"Bearer {token}"}
    endpoint = f"https://graph.microsoft.com/v1.0/chats/{target_chat_id}/members"

    try:
        res = httpx.get(endpoint, headers=headers, timeout=12.0)
        if res.status_code != 200:
            return {"success": False, "error": f"Graph API returned {res.status_code}: {res.text[:150]}"}

        members = res.json().get("value", [])
        discovered_count = 0
        updated_count = 0

        for m in members:
            u_id = m.get("userId") or ""
            d_name = m.get("displayName") or ""
            email = m.get("email") or ""

            # If email is empty, check userPrincipalName if available
            if not email and "@" in str(m.get("userPrincipalName", "")):
                email = m.get("userPrincipalName")

            res_reg = auto_register_member(
                display_name=d_name,
                user_id=u_id,
                email=email,
            )
            if res_reg.get("registered"):
                discovered_count += 1
            elif res_reg.get("updated"):
                updated_count += 1

        # Re-export Excel workbook
        export_members_to_excel()

        db = get_db()
        total_now = db.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]

        return {
            "success": True,
            "chat_id": target_chat_id,
            "found_in_chat": len(members),
            "new_registered": discovered_count,
            "updated": updated_count,
            "total_members": total_now,
            "excel_file": str(MEMBER_FILE_PATH),
        }
    except Exception as err:
        logger.error(f"Error syncing chat members from Teams: {err}")
        return {"success": False, "error": str(err)}


def sync_from_onedrive_sheet(url: Optional[str] = None) -> Dict[str, Any]:
    """Sync database from shared OneDrive Excel sheet, or initialize the sheet if empty."""
    target_url = url or DEFAULT_ONEDRIVE_URL
    file_bytes = download_onedrive_workbook(target_url)

    if not file_bytes:
        # Fallback to local Member.xlsx
        export_members_to_excel()
        return {
            "success": True,
            "source": "local_fallback",
            "message": "OneDrive sheet could not be downloaded; refreshed local Member.xlsx",
            "excel_file": str(MEMBER_FILE_PATH),
        }

    # Parse the downloaded Excel
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    # Check if sheet contains data rows
    rows = list(ws.iter_rows(values_only=True))
    non_empty_rows = [r for r in rows if any(r)]

    if len(non_empty_rows) <= 1:
        # Sheet is blank or only has title — populate it from current roster!
        logger.info("Shared OneDrive Member.xlsx is empty. Exporting full roster into Member.xlsx...")
        export_members_to_excel()
        return {
            "success": True,
            "source": "onedrive",
            "message": "Shared sheet was blank. Generated complete Member.xlsx roster ready for use.",
            "excel_file": str(MEMBER_FILE_PATH),
        }

    # Parse headers and rows
    from src.routes.admin import parse_rows_from_content, execute_members_upsert
    members = parse_rows_from_content(file_bytes=file_bytes, filename="Member.xlsx")

    if members:
        res = execute_members_upsert(members, strategy="upsert")
        export_members_to_excel()
        return {
            "success": True,
            "source": "onedrive",
            "imported_count": res.get("imported_count", 0),
            "updated_count": res.get("updated_count", 0),
            "total_members": res.get("total", 0),
            "excel_file": str(MEMBER_FILE_PATH),
        }

    export_members_to_excel()
    return {
        "success": True,
        "source": "onedrive",
        "message": "No new member rows detected in sheet.",
        "excel_file": str(MEMBER_FILE_PATH),
    }
