"""Teams Roster & Shared Excel (Member.xlsx) Sync Service.

Member.xlsx is the SINGLE SOURCE OF TRUTH (SSOT) for all team member data:
  1. All member data feeds into Member.xlsx first (located in the shared OneDrive folder).
  2. The system strictly depends on Member.xlsx for identities, roles, specialties, and approval permissions.
  3. When new members are discovered in Teams chat/channel roster or when posting messages,
     they are fed directly into Member.xlsx first, which immediately updates the shared cloud workbook.
  4. Database tables (team_members) serve as a synchronized read-cache populated directly from Member.xlsx.
"""
import asyncio
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

# Primary shared OneDrive folder path on user's machine (synced with cloud & Teams)
ONEDRIVE_MEMBER_PATH = Path.home() / "OneDrive" / "Jira-Automation" / "Member.xlsx"
LOCAL_MEMBER_PATH = Path("data/Member.xlsx")

DEFAULT_ONEDRIVE_URL = os.getenv(
    "MEMBER_SHEET_URL",
    "https://1drv.ms/x/c/329A45768254A220/IQCs8zX7occ_T7QD0sO3qzfhAf0ZXEgkcl9IzywZD9Z1Ru0?e=xb5pxk",
)


def get_primary_excel_path() -> Path:
    """Return the active shared Excel file path.
    Prefers the synchronized OneDrive folder so any write or read directly syncs with cloud & Teams.
    """
    if ONEDRIVE_MEMBER_PATH.parent.exists():
        return ONEDRIVE_MEMBER_PATH
    return LOCAL_MEMBER_PATH


MEMBER_FILE_PATH = get_primary_excel_path()


# In-memory cache for fast lookups with mtime invalidation
_cached_members: Optional[List[Dict[str, Any]]] = None
_cached_mtime: float = 0.0


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


_pending_excel_flushes: Optional[bytes] = None


def save_workbook_to_all(wb: openpyxl.Workbook) -> bytes:
    """Save workbook to both the live OneDrive sync path and local data folder."""
    buffer = io.BytesIO()
    wb.save(buffer)
    xlsx_bytes = buffer.getvalue()

    global _pending_excel_flushes

    # 1. Primary OneDrive folder (instantly syncs to cloud & Teams)
    if ONEDRIVE_MEMBER_PATH.parent.exists():
        try:
            with open(ONEDRIVE_MEMBER_PATH, "wb") as f:
                f.write(xlsx_bytes)
            logger.info(f"Saved Member.xlsx to OneDrive sync folder: {ONEDRIVE_MEMBER_PATH}")
            _pending_excel_flushes = None
        except PermissionError:
            _pending_excel_flushes = xlsx_bytes
            logger.warning(
                f"Notice: Member.xlsx is currently open in Microsoft Excel desktop. Changes saved to local cache; queued for OneDrive sync when Excel file lock is released: {ONEDRIVE_MEMBER_PATH}"
            )
        except Exception as err:
            logger.warning(f"Could not write to OneDrive path {ONEDRIVE_MEMBER_PATH}: {err}")

    # 2. Local fallback folder
    try:
        LOCAL_MEMBER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_MEMBER_PATH, "wb") as f:
            f.write(xlsx_bytes)
    except Exception as err:
        logger.warning(f"Could not write to local path {LOCAL_MEMBER_PATH}: {err}")

    # Invalidate cache so next read uses fresh file
    global _cached_members, _cached_mtime
    _cached_members = None
    _cached_mtime = 0.0

    return xlsx_bytes


def read_excel_file_bytes_safe(file_path: Path) -> Optional[bytes]:
    """Read binary file bytes on Windows even when exclusively open in Microsoft Excel desktop.
    Uses Win32 CreateFileW with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE on Windows.
    Falls back to standard open() on other platforms or if Win32 API is unavailable.
    """
    if not file_path.exists():
        return None

    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            GENERIC_READ = 0x80000000
            FILE_SHARE_READ = 0x00000001
            FILE_SHARE_WRITE = 0x00000002
            FILE_SHARE_DELETE = 0x00000004
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80

            p_str = str(file_path.resolve())
            handle = kernel32.CreateFileW(
                p_str,
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
            if handle != -1 and handle != 0xFFFFFFFFFFFFFFFF:
                try:
                    high = wintypes.DWORD()
                    low = kernel32.GetFileSize(handle, ctypes.byref(high))
                    file_size = (high.value << 32) + low
                    if file_size > 0:
                        buf = ctypes.create_string_buffer(file_size)
                        bytes_read = wintypes.DWORD()
                        if kernel32.ReadFile(handle, buf, file_size, ctypes.byref(bytes_read), None):
                            return buf.raw[:bytes_read.value]
                finally:
                    kernel32.CloseHandle(handle)
        except Exception as win_err:
            logger.debug(f"Win32 CreateFile read notice: {win_err}")

    # Standard fallback
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except Exception as std_err:
        logger.debug(f"Standard open read notice: {std_err}")
        return None


def read_members_from_excel(file_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read all members directly from Member.xlsx (The Single Source of Truth).
    Returns list of member dicts including role and level.
    """
    target = file_path or get_primary_excel_path()
    if not target.exists() and target != LOCAL_MEMBER_PATH and LOCAL_MEMBER_PATH.exists():
        target = LOCAL_MEMBER_PATH

    if not target.exists():
        logger.warning(f"Member.xlsx does not exist at {target}. Creating initial sheet...")
        export_members_to_excel(target)

    wb = None
    target_bytes = read_excel_file_bytes_safe(target)
    if target_bytes and len(target_bytes) > 500:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(target_bytes), data_only=True)
            # Synchronize to local fallback
            if target != LOCAL_MEMBER_PATH:
                try:
                    LOCAL_MEMBER_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(LOCAL_MEMBER_PATH, "wb") as lf:
                        lf.write(target_bytes)
                except Exception:
                    pass
        except Exception as load_err:
            logger.debug(f"openpyxl load from memory buffer notice: {load_err}")

    if wb is None:
        try:
            wb = openpyxl.load_workbook(LOCAL_MEMBER_PATH, data_only=True)
        except Exception as fb_err:
            logger.warning(f"Failed to load fallback Member.xlsx: {fb_err}")
            return []
    ws = wb.active

    # Find the header row (first row where a cell contains 'Full Name' or 'Name')
    header_row_idx = None
    col_map = {}

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        row_str = [str(c or "").strip().lower() for c in row]
        if any("name" in c for c in row_str):
            header_row_idx = row_idx
            for col_idx, cell_val in enumerate(row_str, start=1):
                if "name" in cell_val:
                    col_map["name"] = col_idx
                elif "email" in cell_val:
                    col_map["email"] = col_idx
                elif "role" in cell_val:
                    col_map["role"] = col_idx
                elif "level" in cell_val or "tier" in cell_val:
                    col_map["level"] = col_idx
                elif "specialty" in cell_val or "focus" in cell_val:
                    col_map["specialty"] = col_idx
                elif "id" in cell_val or "graph" in cell_val:
                    col_map["user_id"] = col_idx
                elif "approve" in cell_val:
                    col_map["can_approve"] = col_idx
                elif "project" in cell_val or "jira" in cell_val:
                    col_map["project"] = col_idx
                elif "status" in cell_val or "active" in cell_val:
                    col_map["status"] = col_idx
                elif "updated" in cell_val:
                    col_map["updated_at"] = col_idx
            break

    if not header_row_idx:
        logger.warning("Could not locate header row in Member.xlsx. Rebuilding standard layout...")
        export_members_to_excel(target)
        return read_members_from_excel(target)

    members: List[Dict[str, Any]] = []
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row_idx <= header_row_idx:
            continue
        if not any(row):
            continue

        def get_col(key: str, default: Any = "") -> Any:
            idx = col_map.get(key)
            if idx and idx <= len(row):
                val = row[idx - 1]
                return val if val is not None else default
            return default

        name = str(get_col("name", "")).strip()
        if not name or name.lower() == "none":
            continue

        email = str(get_col("email", "")).strip().lower()
        raw_role = str(get_col("role", "UNASSIGNED")).strip().upper()
        if raw_role in ("TL", "PM", "HM", "DEVELOPER", "CLIENT", "UNASSIGNED"):
            role = raw_role
        elif raw_role in ("HIGHER MANAGEMENT", "MANAGEMENT", "DIRECTOR", "VP"):
            role = "HM"
        elif raw_role in ("PROJECT MANAGER",):
            role = "PM"
        elif raw_role in ("TEAM LEAD", "LEAD"):
            role = "TL"
        elif raw_role in ("DEV", "SOFTWARE ENGINEER", "ENGINEER"):
            role = "DEVELOPER"
        else:
            # FR-03: Anyone with an unknown role is treated as UNASSIGNED
            role = "UNASSIGNED"

        level = str(get_col("level", "")).strip()

        # Infer or normalize hierarchy level
        if not level or level in ("-", "none", "null"):
            if role == "TL":
                level = "Level 1"
            elif role == "PM":
                level = "Level 2"
            elif role in ("HM", "HIGHER MANAGEMENT"):
                level = "Level 3"
            else:
                level = "-"

        specialty = str(get_col("specialty", "")).strip()
        user_id = str(get_col("user_id", "")).strip()
        raw_approve = str(get_col("can_approve", "0")).strip().lower()

        # Approval permissions per Specification Role & Hierarchy Matrix:
        # All management levels (Level 1 TL, Level 2 PM, Level 3 HM) can approve/decline tickets
        if role in ("PM", "TL", "HM") or level in ("Level 1", "Level 2", "Level 3"):
            can_approve = True
        elif role == "CLIENT":
            can_approve = raw_approve in ("1", "true", "yes", "y")
        else:
            # AC-03: Reactions from DEVELOPER and UNASSIGNED users trigger nothing (Blocked)
            can_approve = False

        project = str(get_col("project", config.jira.project_key or "SCRUM")).strip()
        status = str(get_col("status", "Active")).strip()
        is_active = status.lower() not in ("inactive", "disabled", "0", "false", "no", "n")
        updated_at = str(get_col("updated_at", "")).strip()

        members.append({
            "display_name": name,
            "email": email,
            "role": role,
            "level": level,
            "specialty": specialty,
            "user_id": user_id,
            "can_approve": can_approve,
            "jira_project": project,
            "status": status,
            "is_active": is_active,
            "updated_at": updated_at,
        })

    return members


def sync_db_from_excel() -> List[Dict[str, Any]]:
    """Synchronize SQLite team_members cache directly from Member.xlsx.
    Ensures database always mirrors the Single Source of Truth in Excel.
    """
    members = read_members_from_excel()
    if not members:
        return []

    db = get_db()
    cursor = db.cursor()

    active_ids = []
    for m in members:
        name = m["display_name"]
        email = m["email"]
        user_id = m["user_id"]
        role = m["role"]
        level = m.get("level") or "-"
        specialty = m["specialty"]
        can_approve = 1 if m["can_approve"] else 0
        is_active = 1 if m["is_active"] else 0

        existing = None
        if user_id:
            existing = cursor.execute(
                "SELECT id FROM team_members WHERE LOWER(user_id) = LOWER(?)", (user_id,)
            ).fetchone()
        if not existing and email:
            existing = cursor.execute(
                "SELECT id FROM team_members WHERE LOWER(email) = LOWER(?)", (email,)
            ).fetchone()
        if not existing:
            existing = cursor.execute(
                "SELECT id FROM team_members WHERE LOWER(display_name) = LOWER(?)", (name.lower(),)
            ).fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE team_members
                SET display_name = ?,
                    email = COALESCE(NULLIF(?, ''), email),
                    user_id = COALESCE(NULLIF(?, ''), user_id),
                    role = ?,
                    level = ?,
                    specialty = ?,
                    can_approve = ?,
                    is_active = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, email, user_id, role, level, specialty, can_approve, is_active, existing["id"]),
            )
            member_id = existing["id"]
        else:
            cursor.execute(
                """
                INSERT INTO team_members (user_id, display_name, email, role, level, specialty, can_approve, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, email, role, level, specialty, can_approve, is_active),
            )
            member_id = cursor.lastrowid
        active_ids.append(member_id)
        m["id"] = member_id

    # Clean up any removed members from SQLite table so it strictly mirrors Member.xlsx
    if active_ids:
        placeholders = ",".join("?" * len(active_ids))
        cursor.execute(f"DELETE FROM team_members WHERE id NOT IN ({placeholders})", active_ids)

    global _cached_members, _cached_mtime
    _cached_members = members
    target = get_primary_excel_path()
    if target.exists():
        try:
            _cached_mtime = os.path.getmtime(target)
        except Exception:
            pass

    logger.info(f"Synchronized database cache from Member.xlsx ({len(members)} members)")
    return members


def get_all_members_from_excel() -> List[Dict[str, Any]]:
    """Get all members with file modification time caching.
    Automatically re-reads from Member.xlsx whenever the Excel file is modified.
    """
    global _cached_members, _cached_mtime
    target_path = get_primary_excel_path()

    mtime = 0.0
    if target_path.exists():
        try:
            mtime = os.path.getmtime(target_path)
        except Exception:
            mtime = 0.0

    if _cached_members is None or mtime > _cached_mtime:
        _cached_members = sync_db_from_excel()
        _cached_mtime = mtime

    return _cached_members or []


def get_member_by_id_or_name(user_id: Optional[str] = None, display_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lookup a member from Member.xlsx data by Graph user_id or display_name."""
    members = get_all_members_from_excel()
    u_id_clean = (user_id or "").strip().lower()
    name_clean = (display_name or "").strip().lower()

    if u_id_clean:
        for m in members:
            if (m.get("user_id") or "").strip().lower() == u_id_clean:
                return m

    if name_clean:
        # Exact match
        for m in members:
            if (m.get("display_name") or "").strip().lower() == name_clean:
                return m
        # First name / partial match / fuzzy match
        import difflib
        first_clean = name_clean.split()[0] if name_clean else ""
        for m in members:
            m_name = (m.get("display_name") or "").strip().lower()
            m_first = m_name.split()[0] if m_name else ""
            if first_clean and m_first and (first_clean == m_first or first_clean in m_name or name_clean in m_name):
                return m
            if first_clean and m_first and difflib.SequenceMatcher(None, first_clean, m_first).ratio() >= 0.7:
                return m
            if name_clean and m_name and difflib.SequenceMatcher(None, name_clean, m_name).ratio() >= 0.7:
                return m

    return None


def get_active_pms() -> List[Dict[str, Any]]:
    """Retrieve all active Project Managers (Level 2) directly from Member.xlsx."""
    members = get_all_members_from_excel()
    return [
        m for m in members
        if m.get("is_active") and (
            (m.get("role") or "").upper() == "PM" or
            (m.get("level") or "").upper() in ("LEVEL 2", "2", "L2")
        )
    ]


def get_active_tls() -> List[Dict[str, Any]]:
    """Retrieve all active Team Leads (Level 1) directly from Member.xlsx."""
    members = get_all_members_from_excel()
    return [
        m for m in members
        if m.get("is_active") and (
            (m.get("role") or "").upper() == "TL" or
            (m.get("level") or "").upper() in ("LEVEL 1", "1", "L1")
        )
    ]


def get_active_hms() -> List[Dict[str, Any]]:
    """Retrieve all active Higher Management members (Level 3) directly from Member.xlsx."""
    members = get_all_members_from_excel()
    return [
        m for m in members
        if m.get("is_active") and (
            (m.get("role") or "").upper() in ("HM", "HIGHER MANAGEMENT") or
            (m.get("level") or "").upper() in ("LEVEL 3", "3", "L3")
        )
    ]


def get_active_authorized_approvers() -> List[Dict[str, Any]]:
    """Retrieve all active members authorized to approve/decline tickets from Member.xlsx.
    Includes management tiers: Level 1 (TL), Level 2 (PM), Level 3 (HM), or members with explicit can_approve=True.
    Strictly excludes DEVELOPER, UNASSIGNED, and CLIENT (unless can_approve=True).
    """
    members = get_all_members_from_excel()
    approvers = []
    seen = set()
    for m in members:
        if not m.get("is_active"):
            continue
        role = (m.get("role") or "").upper().strip()
        level = str(m.get("level") or "").upper().strip()
        can_approve = bool(m.get("can_approve"))

        if role in ("DEVELOPER", "UNASSIGNED", "CLIENT") and not can_approve:
            continue

        if role in ("PM", "TL", "HM") or level in ("LEVEL 1", "LEVEL 2", "LEVEL 3", "1", "2", "3", "L1", "L2", "L3") or can_approve:
            uid = m.get("user_id") or m.get("display_name")
            if uid and uid not in seen:
                seen.add(uid)
                approvers.append(m)
    return approvers


def get_members_by_level(level: str) -> List[Dict[str, Any]]:
    """Retrieve all active members assigned to a specific hierarchy level."""
    clean_lvl = (level or "").strip().upper()
    if clean_lvl in ("1", "LEVEL 1", "L1", "TL"):
        return get_active_tls()
    elif clean_lvl in ("2", "LEVEL 2", "L2", "PM"):
        return get_active_pms()
    elif clean_lvl in ("3", "LEVEL 3", "L3", "HM"):
        return get_active_hms()
    members = get_all_members_from_excel()
    return [
        m for m in members
        if m.get("is_active") and (m.get("level") or "").strip().upper() == clean_lvl
    ]


def get_active_pm_from_excel() -> Dict[str, str]:
    """Retrieve primary active Project Manager (PM) directly from Member.xlsx data.
    Never relies on hardcoded names. Supports multiple PMs by returning primary/first.
    """
    pms = get_active_pms()
    if pms:
        first = pms[0]
        return {
            "name": first.get("display_name") or "Project Manager",
            "email": first.get("email") or "",
            "user_id": first.get("user_id") or "",
            "level": first.get("level") or "Level 2",
        }

    # 2. Look for approver if no PM specified
    members = get_all_members_from_excel()
    for m in members:
        if m.get("is_active") and m.get("can_approve"):
            return {
                "name": m.get("display_name") or "Approver",
                "email": m.get("email") or "",
                "user_id": m.get("user_id") or "",
                "level": m.get("level") or "-",
            }

    # 3. Fallback
    return {
        "name": "Project Manager",
        "email": "",
        "user_id": "",
        "level": "Level 2",
    }


def feed_member_to_excel(
    display_name: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    level: Optional[str] = None,
    specialty: Optional[str] = None,
    can_approve: Optional[bool] = None,
    jira_project: Optional[str] = None,
) -> Dict[str, Any]:
    """Feed member data directly into Member.xlsx FIRST (The SSOT).
    If the member already exists, updates any missing fields.
    If the member is new, appends them to the sheet and formats the row.
    Immediately updates the shared OneDrive spreadsheet and syncs database cache.
    """
    name_clean = (display_name or "").strip()
    if not name_clean:
        return {"success": False, "reason": "Empty display name"}

    user_id_clean = (user_id or "").strip()
    email_clean = (email or "").strip().lower()

    # If user_id is provided but profile fields (name, email, specialty) are missing, resolve via Graph API
    if user_id_clean and (not email_clean or not specialty or not name_clean):
        try:
            from src.graph_client import get_user_profile
            prof = get_user_profile(user_id_clean)
            if prof:
                if not name_clean and prof.get("displayName"):
                    name_clean = prof.get("displayName").strip()
                if not email_clean:
                    email_clean = (prof.get("mail") or prof.get("userPrincipalName") or "").strip().lower()
                if not specialty and prof.get("jobTitle"):
                    specialty = prof.get("jobTitle").strip()
        except Exception:
            pass

    # Determine role dynamically without hardcoding names
    assigned_role = role
    if not assigned_role:
        spec_lower = (specialty or "").lower()
        if "client" in spec_lower or "product owner" in spec_lower:
            assigned_role = "CLIENT"
        elif "pm" in spec_lower or "project manager" in spec_lower or "scrum" in spec_lower:
            assigned_role = "PM"
        elif "tl" in spec_lower or "team lead" in spec_lower:
            assigned_role = "TL"
        elif "developer" in spec_lower or "engineer" in spec_lower:
            assigned_role = "DEVELOPER"
        else:
            # Per FR-03: newly discovered or unassigned chat members default to UNASSIGNED
            assigned_role = "UNASSIGNED"
    assigned_role = assigned_role.upper()

    # Determine hierarchy level
    assigned_level = level
    if not assigned_level or assigned_level in ("-", "none", "null"):
        if assigned_role == "TL":
            assigned_level = "Level 1"
        elif assigned_role == "PM":
            assigned_level = "Level 2"
        elif assigned_role in ("HM", "HIGHER MANAGEMENT"):
            assigned_level = "Level 3"
        else:
            assigned_level = "-"

    # Specialty
    if not specialty:
        if assigned_role == "CLIENT":
            specialty = "Client / Product Owner"
        elif assigned_role == "PM":
            specialty = "Project Manager / Scrum Master"
        elif assigned_role == "TL":
            specialty = "Technical Team Lead"
        elif assigned_role in ("HM", "HIGHER MANAGEMENT"):
            specialty = "Higher Management"
        elif assigned_role == "DEVELOPER":
            specialty = "Software Engineer"
        else:
            specialty = "Pending Role Assignment"

    if can_approve is None:
        can_approve = assigned_role in ("PM", "TL", "HM") or assigned_level in ("Level 1", "Level 2", "Level 3")

    target_path = get_primary_excel_path()
    if not target_path.exists():
        export_members_to_excel(target_path)

    try:
        wb = openpyxl.load_workbook(target_path)
    except (PermissionError, OSError):
        logger.warning(f"File {target_path} is locked by another process. Reading from local copy {LOCAL_MEMBER_PATH}")
        wb = openpyxl.load_workbook(LOCAL_MEMBER_PATH)
    ws = wb.active

    # Find header row and column mapping
    header_row_idx = 4
    col_map = {
        "name": 1,
        "email": 2,
        "role": 3,
        "level": 4,
        "specialty": 5,
        "user_id": 6,
        "can_approve": 7,
        "project": 8,
        "status": 9,
        "updated": 10,
    }

    has_level_col = False
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        row_str = [str(c or "").strip().lower() for c in row]
        if any("name" in c for c in row_str):
            header_row_idx = r_idx
            for c_idx, val in enumerate(row_str, start=1):
                if "name" in val:
                    col_map["name"] = c_idx
                elif "email" in val:
                    col_map["email"] = c_idx
                elif "role" in val:
                    col_map["role"] = c_idx
                elif "level" in val or "tier" in val:
                    col_map["level"] = c_idx
                    has_level_col = True
                elif "specialty" in val or "focus" in val:
                    col_map["specialty"] = c_idx
                elif "id" in val or "graph" in val:
                    col_map["user_id"] = c_idx
                elif "approve" in val:
                    col_map["can_approve"] = c_idx
                elif "project" in val or "jira" in val:
                    col_map["project"] = c_idx
                elif "status" in val:
                    col_map["status"] = c_idx
                elif "updated" in val:
                    col_map["updated"] = c_idx
            break

    # If sheet lacks Level column, re-export standard 10-column layout
    if not has_level_col:
        export_members_to_excel(target_path)
        wb = openpyxl.load_workbook(target_path)
        ws = wb.active
        col_map = {
            "name": 1, "email": 2, "role": 3, "level": 4, "specialty": 5,
            "user_id": 6, "can_approve": 7, "project": 8, "status": 9, "updated": 10
        }

    # Look for existing member row
    existing_row_idx = None
    max_row = ws.max_row or header_row_idx

    for r_idx in range(header_row_idx + 1, max_row + 1):
        cell_name = str(ws.cell(row=r_idx, column=col_map["name"]).value or "").strip()
        cell_email = str(ws.cell(row=r_idx, column=col_map["email"]).value or "").strip().lower()
        cell_uid = str(ws.cell(row=r_idx, column=col_map["user_id"]).value or "").strip()

        if user_id_clean and cell_uid and cell_uid.lower() == user_id_clean.lower():
            existing_row_idx = r_idx
            break
        if email_clean and cell_email and cell_email == email_clean:
            existing_row_idx = r_idx
            break
        if cell_name and cell_name.lower() == name_clean.lower():
            existing_row_idx = r_idx
            break

    thin_border = Border(
        left=Side(style="thin", color="334155"),
        right=Side(style="thin", color="334155"),
        top=Side(style="thin", color="334155"),
        bottom=Side(style="thin", color="334155"),
    )

    role_colors = {
        "CLIENT": ("EFF6FF", "1D4ED8"),
        "PM": ("FEF3C7", "B45309"),
        "TL": ("F0FDF4", "15803D"),
        "HM": ("FDF2F8", "BE185D"),
        "DEVELOPER": ("ECFDF5", "047857"),
        "UNASSIGNED": ("F1F5F9", "64748B"),
        "ADMIN": ("F5F3FF", "6D28D9"),
    }

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if existing_row_idx:
        # Update missing fields in existing Excel row
        row_idx = existing_row_idx
        changed = False
        if user_id_clean and not ws.cell(row=row_idx, column=col_map["user_id"]).value:
            ws.cell(row=row_idx, column=col_map["user_id"]).value = user_id_clean
            changed = True
        if email_clean and not ws.cell(row=row_idx, column=col_map["email"]).value:
            ws.cell(row=row_idx, column=col_map["email"]).value = email_clean
            changed = True
        if specialty and (not ws.cell(row=row_idx, column=col_map["specialty"]).value or ws.cell(row=row_idx, column=col_map["specialty"]).value == "Software Engineer"):
            ws.cell(row=row_idx, column=col_map["specialty"]).value = specialty
            changed = True
        if role and ws.cell(row=row_idx, column=col_map["role"]).value != assigned_role:
            ws.cell(row=row_idx, column=col_map["role"]).value = assigned_role
            changed = True
        if "level" in col_map and level and ws.cell(row=row_idx, column=col_map["level"]).value != assigned_level:
            ws.cell(row=row_idx, column=col_map["level"]).value = assigned_level
            changed = True

        if changed:
            ws.cell(row=row_idx, column=col_map["updated"]).value = now_str
            save_workbook_to_all(wb)
            sync_db_from_excel()
            logger.info(f"Updated member row in Member.xlsx: {name_clean}")
            return {"success": True, "action": "updated", "name": name_clean, "role": assigned_role, "level": assigned_level}

        return {"success": True, "action": "unchanged", "name": name_clean, "role": assigned_role, "level": assigned_level}

    # Append new row to Member.xlsx
    new_row_idx = max_row + 1
    # Check if max_row is empty
    if max_row > header_row_idx:
        val_at_max = ws.cell(row=max_row, column=col_map["name"]).value
        if not val_at_max:
            new_row_idx = max_row

    ws.row_dimensions[new_row_idx].height = 20
    ws.cell(row=new_row_idx, column=col_map["name"], value=name_clean)
    ws.cell(row=new_row_idx, column=col_map["email"], value=email_clean)
    ws.cell(row=new_row_idx, column=col_map["role"], value=assigned_role)
    if "level" in col_map:
        ws.cell(row=new_row_idx, column=col_map["level"], value=assigned_level)
    ws.cell(row=new_row_idx, column=col_map["specialty"], value=specialty)
    ws.cell(row=new_row_idx, column=col_map["user_id"], value=user_id_clean)
    ws.cell(row=new_row_idx, column=col_map["can_approve"], value="1" if can_approve else "0")
    ws.cell(row=new_row_idx, column=col_map["project"], value=jira_project or config.jira.project_key or "SCRUM")
    ws.cell(row=new_row_idx, column=col_map["status"], value="Active")
    ws.cell(row=new_row_idx, column=col_map["updated"], value=now_str)

    # Style cells
    bg_color, fg_color = role_colors.get(assigned_role, ("F8FAFC", "334155"))
    total_cols = max(col_map.values())
    for col_i in range(1, total_cols + 1):
        c = ws.cell(row=new_row_idx, column=col_i)
        c.font = Font(name="Segoe UI", size=9.5)
        c.border = thin_border
        if col_i in (col_map["name"], col_map["email"], col_map["specialty"]):
            c.alignment = Alignment(horizontal="left", vertical="center")
        else:
            c.alignment = Alignment(horizontal="center", vertical="center")
        if col_i == col_map["role"]:
            c.fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")
            c.font = Font(name="Segoe UI", size=9.5, bold=True, color=fg_color)

    # Update subtitle timestamp
    ws.cell(row=2, column=1).value = f"Single Source of Truth for Team Members, Roles & Permissions | Last Updated: {now_str}"

    save_workbook_to_all(wb)
    sync_db_from_excel()

    logger.info(
        f"Fed member '{name_clean}' into Member.xlsx as {assigned_role} ({assigned_level})",
        extra={"event": "MEMBER_FED_TO_EXCEL", "member_name": name_clean, "role": assigned_role, "level": assigned_level},
    )
    return {"success": True, "action": "created", "name": name_clean, "role": assigned_role, "level": assigned_level}


def auto_register_member(
    display_name: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    level: Optional[str] = None,
    specialty: Optional[str] = None,
    can_approve: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compatibility alias: Feeds member data directly into Member.xlsx first."""
    return feed_member_to_excel(
        display_name=display_name,
        user_id=user_id,
        email=email,
        role=role,
        level=level,
        specialty=specialty,
        can_approve=can_approve,
    )


def remove_member_from_excel(
    user_id: Optional[str] = None,
    display_name: Optional[str] = None,
) -> bool:
    """Remove a member row from Member.xlsx when they leave Teams or are deleted."""
    name_clean = (display_name or "").strip().lower()
    u_id_clean = (user_id or "").strip().lower()
    if not name_clean and not u_id_clean:
        return False

    target_path = get_primary_excel_path()
    if not target_path.exists() and target_path != LOCAL_MEMBER_PATH and LOCAL_MEMBER_PATH.exists():
        target_path = LOCAL_MEMBER_PATH
    if not target_path.exists():
        return False

    try:
        wb = openpyxl.load_workbook(target_path)
    except (PermissionError, OSError):
        logger.warning(f"File {target_path} is locked by another process. Reading from local copy {LOCAL_MEMBER_PATH}")
        if not LOCAL_MEMBER_PATH.exists():
            return False
        wb = openpyxl.load_workbook(LOCAL_MEMBER_PATH)

    ws = wb.active

    header_row_idx = 4
    col_map = {"name": 1, "user_id": 5}
    for r in range(1, min(10, (ws.max_row or 1) + 1)):
        row_vals = [str(ws.cell(row=r, column=c).value or "").lower() for c in range(1, 12)]
        if any("full name" in v or "name" in v for v in row_vals):
            header_row_idx = r
            for c_idx, val in enumerate(row_vals, start=1):
                if "name" in val:
                    col_map["name"] = c_idx
                elif "id" in val or "graph" in val:
                    col_map["user_id"] = c_idx
            break

    target_row_idx = None
    max_row = ws.max_row or header_row_idx
    name_norm = re.sub(r"\s+", " ", name_clean) if name_clean else ""

    for r_idx in range(header_row_idx + 1, max_row + 1):
        raw_cell_name = str(ws.cell(row=r_idx, column=col_map["name"]).value or "").strip()
        cell_name = re.sub(r"\s+", " ", raw_cell_name).lower()
        cell_uid = str(ws.cell(row=r_idx, column=col_map["user_id"]).value or "").strip().lower()

        if u_id_clean and cell_uid and cell_uid == u_id_clean:
            target_row_idx = r_idx
            break
        if name_norm and cell_name and (cell_name == name_norm or name_norm in cell_name or cell_name in name_norm):
            target_row_idx = r_idx
            break

    removed = False
    if target_row_idx:
        removed_name = ws.cell(row=target_row_idx, column=col_map["name"]).value
        ws.delete_rows(target_row_idx)
        save_workbook_to_all(wb)
        sync_db_from_excel()
        logger.info(f"🗑️ Removed departed member from Member.xlsx: {removed_name}")
        removed = True

    # Always ensure removal from SQLite database even if Excel file was locked or missing row
    try:
        db = get_db()
        cursor = db.cursor()
        if u_id_clean:
            cursor.execute("DELETE FROM team_members WHERE LOWER(user_id) = ?", (u_id_clean,))
        if name_norm:
            cursor.execute("DELETE FROM team_members WHERE LOWER(display_name) = ? OR LOWER(display_name) LIKE ?", (name_norm, f"%{name_norm}%"))
        db.commit()
    except Exception as db_err:
        logger.debug(f"SQLite cleanup error for departed member: {db_err}")

    return removed


def export_members_to_excel(output_path: Optional[Path] = None) -> bytes:
    """Generate or refresh a clean, styled Member.xlsx workbook with 10 columns (including Level).
    Writes to both OneDrive sync folder and local project folder.
    """
    target_path = output_path or get_primary_excel_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    db = get_db()
    rows = db.execute(
        """SELECT * FROM team_members
           ORDER BY CASE role
               WHEN 'HM' THEN 1
               WHEN 'PM' THEN 2
               WHEN 'TL' THEN 3
               WHEN 'CLIENT' THEN 4
               WHEN 'DEVELOPER' THEN 5
               ELSE 6 END, id ASC"""
    ).fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Team Directory"
    ws.views.sheetView[0].showGridLines = True

    # Title block
    ws.merge_cells("A1:J1")
    title_cell = ws.cell(row=1, column=1)
    title_cell.value = f"JIRA AUTOMATION HUB - TEAM ROSTER & ROLES DIRECTORY (Target: {config.jira.project_key or 'SCRUM'})"
    title_cell.font = Font(name="Segoe UI", size=13, bold=True, color="FFFFFF")
    title_cell.fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Subtitle with timestamp
    ws.merge_cells("A2:J2")
    sub_cell = ws.cell(row=2, column=1)
    sub_cell.value = f"Single Source of Truth for Team Members, Roles, Hierarchy Levels & Permissions | Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    sub_cell.font = Font(name="Segoe UI", size=9, italic=True, color="94A3B8")
    sub_cell.fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    sub_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20

    # Headers
    headers = [
        "Full Name",
        "Email Address",
        "Assigned Role",
        "Level",
        "Specialty / Focus Area",
        "Teams Graph User ID",
        "Can Approve (1/0)",
        "Jira Project",
        "Status",
        "Last Updated",
    ]
    ws.append([])  # row 3
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

    role_colors = {
        "CLIENT": ("EFF6FF", "1D4ED8"),
        "PM": ("FEF3C7", "B45309"),
        "TL": ("F0FDF4", "15803D"),
        "HM": ("FDF2F8", "BE185D"),
        "DEVELOPER": ("ECFDF5", "047857"),
        "UNASSIGNED": ("F1F5F9", "64748B"),
        "ADMIN": ("F5F3FF", "6D28D9"),
    }

    current_row = 5
    for r in rows:
        role = (r["role"] or "UNASSIGNED").upper()
        level = r["level"] or (
            "Level 1" if role == "TL" else (
                "Level 2" if role == "PM" else (
                    "Level 3" if role in ("HM", "HIGHER MANAGEMENT") else "-"
                )
            )
        )
        bg_color, fg_color = role_colors.get(role, ("F8FAFC", "334155"))
        row_fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")

        data_values = [
            r["display_name"] or "",
            r["email"] or "",
            role,
            level,
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

            if col_idx in (1, 2, 5):
                cell.alignment = Alignment(horizontal="left", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center")

            if col_idx == 3:
                cell.fill = row_fill
                cell.font = Font(name="Segoe UI", size=9.5, bold=True, color=fg_color)
            elif col_idx == 4:
                cell.font = Font(name="Segoe UI", size=9.5, bold=True)

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

    # In-Cell Dropdown Data Validations
    from openpyxl.worksheet.datavalidation import DataValidation

    # 1. Role Dropdown (TL, Client, PM, Developer, HM, Unassigned) for Column C
    role_dv = DataValidation(type="list", formula1='"TL, Client, PM, Developer, HM, Unassigned"', allow_blank=True)
    role_dv.error = "Please choose a valid role: TL, Client, PM, Developer, HM, or Unassigned"
    role_dv.errorTitle = "Invalid Role"
    role_dv.prompt = "Choose role: TL, Client, PM, Developer, HM, or Unassigned"
    role_dv.promptTitle = "Role Selection"
    ws.add_data_validation(role_dv)
    role_dv.add("C5:C500")

    # 2. Level Dropdown (Level 1, Level 2, Level 3, -) for Column D
    level_dv = DataValidation(type="list", formula1='"Level 1, Level 2, Level 3, -"', allow_blank=True)
    level_dv.error = "Please choose a valid level: Level 1, Level 2, Level 3, or -"
    level_dv.errorTitle = "Invalid Level"
    level_dv.prompt = "Choose hierarchy level: Level 1 (TL), Level 2 (PM), Level 3 (HM), or -"
    level_dv.promptTitle = "Level Selection"
    ws.add_data_validation(level_dv)
    level_dv.add("D5:D500")

    # 3. Can Approve Dropdown (1, 0) for Column G
    approve_dv = DataValidation(type="list", formula1='"1, 0"', allow_blank=True)
    approve_dv.error = "Enter 1 for approval permissions, or 0"
    approve_dv.errorTitle = "Invalid Permission"
    ws.add_data_validation(approve_dv)
    approve_dv.add("G5:G500")

    # 4. Status Dropdown (Active, Inactive) for Column I
    status_dv = DataValidation(type="list", formula1='"Active, Inactive"', allow_blank=True)
    ws.add_data_validation(status_dv)
    status_dv.add("I5:I500")

    return save_workbook_to_all(wb)


def sync_teams_chat_roster(chat_id: Optional[str] = None, team_id: Optional[str] = None) -> Dict[str, Any]:
    """Poll Microsoft Graph API for all members in the Teams chat and team/channel roster
    and feed newly arrived or updated members directly into Member.xlsx first!
    New members default to Role: 'Unassigned', Level: '-', can_approve: False.
    Departed members are removed immediately from Member.xlsx and database.
    """
    token = get_access_token()
    if not token:
        return {"success": False, "error": "Could not acquire Microsoft Graph token"}

    headers = {"Authorization": f"Bearer {token}"}
    target_chat_id = chat_id or config.teams.chat_id
    target_team_id = team_id or config.teams.team_id

    endpoints = []
    if target_chat_id:
        endpoints.append(f"https://graph.microsoft.com/v1.0/chats/{target_chat_id}/members")
    if target_team_id:
        endpoints.append(f"https://graph.microsoft.com/v1.0/teams/{target_team_id}/members")

    if not endpoints:
        return {"success": False, "error": "Neither Teams chat ID nor team ID configured"}

    discovered_count = 0
    updated_count = 0
    all_roster_members = []
    seen_ids = set()

    for endpoint in endpoints:
        try:
            res = httpx.get(endpoint, headers=headers, timeout=12.0)
            if res.status_code == 200:
                members = res.json().get("value", [])
                for m in members:
                    u_id = m.get("userId") or m.get("id") or ""
                    if u_id and u_id in seen_ids:
                        continue
                    if u_id:
                        seen_ids.add(u_id)
                    all_roster_members.append(m)
            else:
                logger.debug(f"Roster fetch HTTP {res.status_code} on {endpoint}")
        except Exception as err:
            logger.debug(f"Roster fetch error on {endpoint}: {err}")

    for m in all_roster_members:
        u_id = m.get("userId") or m.get("id") or ""
        d_name = m.get("displayName") or ""
        email = m.get("email") or ""

        if not email and "@" in str(m.get("userPrincipalName", "")):
            email = m.get("userPrincipalName")

        # Per verified requirement: newly joined members default to UNASSIGNED / '-',
        # while existing members preserve their configured role and hierarchy level.
        res_feed = feed_member_to_excel(
            display_name=d_name,
            user_id=u_id,
            email=email,
            role=None,
            level=None,
            can_approve=None,
        )
        if res_feed.get("action") == "created":
            discovered_count += 1
            logger.info(f"✨ New member discovered in Teams chat and added to Member.xlsx as Unassigned: {d_name or u_id}")
        elif res_feed.get("action") == "updated":
            updated_count += 1

    # Auto-prune departed members from Member.xlsx immediately:
    removed_count = 0
    if all_roster_members:
        roster_uids = {
            str(m.get("userId") or m.get("id") or "").strip().lower()
            for m in all_roster_members
            if (m.get("userId") or m.get("id"))
        }
        roster_names = {
            re.sub(r"\s+", " ", str(m.get("displayName") or "")).strip().lower()
            for m in all_roster_members
            if m.get("displayName")
        }

        current_excel_members = read_members_from_excel()
        for em in current_excel_members:
            em_uid = (em.get("user_id") or "").strip().lower()
            em_name = re.sub(r"\s+", " ", (em.get("display_name") or "")).strip().lower()

            in_roster = False
            if em_uid and em_uid in roster_uids:
                in_roster = True
            elif em_name and em_name in roster_names:
                in_roster = True

            if not in_roster:
                logger.info(f"🗑️ Detected departed member '{em.get('display_name')}' (no longer in Teams chat roster); removing from Member.xlsx immediately")
                if remove_member_from_excel(user_id=em.get("user_id"), display_name=em.get("display_name")):
                    removed_count += 1

    all_members = get_all_members_from_excel()

    return {
        "success": True,
        "chat_id": target_chat_id,
        "found_in_chat": len(all_roster_members),
        "new_registered": discovered_count,
        "updated": updated_count,
        "removed": removed_count,
        "total_members": len(all_members),
        "excel_file": str(get_primary_excel_path()),
    }


# Background periodic monitor for roster auto-discovery (every 15s)
_roster_monitor_task: Optional[asyncio.Task] = None
ROSTER_MONITOR_INTERVAL_SECONDS = 15  # Rapid 15s interval for accurate join/leave tracking


async def _roster_monitor_loop():
    """Background loop that polls Teams roster every 15s to ensure joins and leaves are reflected in Member.xlsx."""
    logger.info(
        f"Teams roster background monitor started (polling every {ROSTER_MONITOR_INTERVAL_SECONDS}s)",
        extra={"event": "ROSTER_MONITOR_START"},
    )
    while True:
        try:
            await asyncio.sleep(ROSTER_MONITOR_INTERVAL_SECONDS)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, sync_teams_chat_roster)
        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.debug(f"Roster monitor check: {err}")


def start_roster_monitor():
    """Start the periodic background roster monitor."""
    global _roster_monitor_task
    if _roster_monitor_task is None or _roster_monitor_task.done():
        try:
            loop = asyncio.get_running_loop()
            _roster_monitor_task = loop.create_task(_roster_monitor_loop())
        except RuntimeError:
            pass


def stop_roster_monitor():
    """Stop the background roster monitor."""
    global _roster_monitor_task
    if _roster_monitor_task and not _roster_monitor_task.done():
        _roster_monitor_task.cancel()
        _roster_monitor_task = None


# Proactive background file watcher for instant manual Excel edits
_excel_watcher_task: Optional[asyncio.Task] = None
EXCEL_WATCHER_INTERVAL_SECONDS = 3  # Check disk every 3 seconds for manual sheet edits


async def _excel_file_watcher_loop():
    """Background loop that polls Member.xlsx on disk every 3s:
    1. Detects manual changes (role/level/member changes) in Excel desktop or Excel Online.
    2. Instantly syncs SQLite team_members database cache.
    3. Automatically flushes any deferred writes waiting for Excel file lock release.
    """
    logger.info(
        f"Proactive Member.xlsx file watcher started (polling disk every {EXCEL_WATCHER_INTERVAL_SECONDS}s)",
        extra={"event": "EXCEL_WATCHER_START"},
    )
    global _cached_mtime, _pending_excel_flushes

    while True:
        try:
            await asyncio.sleep(EXCEL_WATCHER_INTERVAL_SECONDS)

            # 1. Flush any pending writes if Excel was previously locked
            if _pending_excel_flushes is not None and ONEDRIVE_MEMBER_PATH.parent.exists():
                try:
                    with open(ONEDRIVE_MEMBER_PATH, "wb") as f:
                        f.write(_pending_excel_flushes)
                    logger.info(f"✅ Successfully flushed queued Member.xlsx changes to OneDrive: {ONEDRIVE_MEMBER_PATH}")
                    _pending_excel_flushes = None
                except PermissionError:
                    pass  # Excel still open
                except Exception as ex:
                    logger.debug(f"Pending flush check: {ex}")

            # 2. Check file modification time on disk
            target = get_primary_excel_path()
            if target.exists():
                try:
                    current_mtime = os.path.getmtime(target)
                    if _cached_mtime > 0.0 and current_mtime > _cached_mtime:
                        logger.info(
                            f"⚡ Detected manual modification in {target.name}; synchronizing system permissions & database cache immediately",
                            extra={"event": "MANUAL_SHEET_EDIT_DETECTED", "file": str(target)},
                        )
                        loop = asyncio.get_running_loop()
                        updated_members = await loop.run_in_executor(None, sync_db_from_excel)
                        _cached_members = updated_members
                        _cached_mtime = current_mtime

                        # Proactively broadcast ROSTER_UPDATED to all connected dashboards via WebSocket
                        try:
                            from src.services.broadcaster import broadcast_message
                            await broadcast_message({
                                "type": "ROSTER_UPDATED",
                                "message": f"Team roster synchronized from Member.xlsx ({len(updated_members)} members)",
                                "members": updated_members,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception as b_err:
                            logger.debug(f"Broadcast roster error notice: {b_err}")
                    elif _cached_mtime == 0.0:
                        _cached_mtime = current_mtime
                except Exception as watch_err:
                    logger.debug(f"Excel file watcher check notice: {watch_err}")
        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.debug(f"Excel file watcher loop error: {err}")


def start_excel_watcher():
    """Start the proactive background Member.xlsx file watcher."""
    global _excel_watcher_task
    if _excel_watcher_task is None or _excel_watcher_task.done():
        try:
            loop = asyncio.get_running_loop()
            _excel_watcher_task = loop.create_task(_excel_file_watcher_loop())
        except RuntimeError:
            pass


def stop_excel_watcher():
    """Stop the background Member.xlsx file watcher."""
    global _excel_watcher_task
    if _excel_watcher_task and not _excel_watcher_task.done():
        _excel_watcher_task.cancel()
        _excel_watcher_task = None


def sync_from_onedrive_sheet(url: Optional[str] = None) -> Dict[str, Any]:
    """Sync database from shared OneDrive Excel sheet, or download from URL."""
    target_url = url or DEFAULT_ONEDRIVE_URL

    # If the local OneDrive sync folder exists on disk, read directly from it!
    if ONEDRIVE_MEMBER_PATH.exists():
        members = sync_db_from_excel()
        return {
            "success": True,
            "source": "onedrive_local_sync",
            "message": f"Synchronized from local OneDrive file ({len(members)} members)",
            "excel_file": str(ONEDRIVE_MEMBER_PATH),
            "total_members": len(members),
        }

    # Otherwise download from cloud URL
    file_bytes = download_onedrive_workbook(target_url)
    if not file_bytes:
        members = sync_db_from_excel()
        return {
            "success": True,
            "source": "local_fallback",
            "message": "OneDrive sheet could not be downloaded; refreshed local Member.xlsx",
            "excel_file": str(LOCAL_MEMBER_PATH),
            "total_members": len(members),
        }

    # Save downloaded bytes
    with open(LOCAL_MEMBER_PATH, "wb") as f:
        f.write(file_bytes)

    members = sync_db_from_excel()
    return {
        "success": True,
        "source": "onedrive_download",
        "message": f"Downloaded and synchronized from OneDrive URL ({len(members)} members)",
        "excel_file": str(LOCAL_MEMBER_PATH),
        "total_members": len(members),
    }
