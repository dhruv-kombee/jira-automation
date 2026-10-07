from typing import Optional, List, Dict, Any
from src.config import config
from src.logger import logger
from src.services.member_sync_service import (
    get_member_by_id_or_name,
    get_all_members_from_excel,
    get_active_pm_from_excel,
)


class Roles:
    CLIENT = "CLIENT"
    PM = "PM"
    DEVELOPER = "DEVELOPER"
    ADMIN = "ADMIN"
    UNKNOWN = "UNKNOWN"


def identify_sender_role(user_id: Optional[str] = None, display_name: Optional[str] = None) -> str:
    """Identify the role of a user dynamically from Member.xlsx (The Single Source of Truth).
    Falls back to configured environment variables if not yet present in Excel.
    """
    # 1. Check Member.xlsx (Single Source of Truth)
    try:
        member = get_member_by_id_or_name(user_id=user_id, display_name=display_name)
        if member and member.get("role"):
            role = member["role"].strip().upper()
            if role in (Roles.CLIENT, Roles.PM, Roles.DEVELOPER, Roles.ADMIN):
                return role
    except Exception as err:
        logger.debug(f"Error querying members from Excel: {err}")

    # 2. Match by configured GUIDs in config
    if user_id:
        normalized = user_id.strip().lower()
        if config.roles.client and config.roles.client.strip().lower() == normalized:
            return Roles.CLIENT
        if config.roles.pm and config.roles.pm.strip().lower() == normalized:
            return Roles.PM
        if config.roles.developer and config.roles.developer.strip().lower() == normalized:
            return Roles.DEVELOPER

    logger.info(
        "Unknown sender",
        extra={
            "event": "UNKNOWN_SENDER",
            "userId": user_id,
            "displayName": display_name,
        },
    )

    return Roles.UNKNOWN


TICKET_APPROVAL_NAMES = {
    "admission ticket",
    "admission tickets",
    "admission_ticket",
    "admission_tickets",
    ":admission_tickets:",
    ":admission_ticket:",
    ":ticket:",
    ":tickets:",
    "ticket",
    "tickets",
}


def is_ticket_approval_reaction(reaction_type: Optional[str]) -> bool:
    """Check if reaction is specifically 'Admission tickets' (🎟️ / 🎟) or 'Ticket' (🎫)."""
    if not reaction_type:
        return False
    raw = str(reaction_type).strip()
    lower = raw.lower()

    # 1. Direct emoji character check (handles standard and variation selector sequences)
    if "🎟" in raw or "🎫" in raw or "\U0001f39f" in raw or "\U0001f3ab" in raw:
        return True

    # 2. Text or shortcode check (case-insensitive)
    cleaned = lower.replace("-", " ").replace("_", " ").strip(": ")
    if cleaned in {"admission ticket", "admission tickets", "ticket", "tickets"}:
        return True

    for name in TICKET_APPROVAL_NAMES:
        if lower == name or f":{name}:" == lower:
            return True

    return False


def is_user_authorized_approver(
    user_id: Optional[str] = None,
    display_name: Optional[str] = None,
    allow_client: Optional[bool] = None,
) -> bool:
    """Check if a specific user has approval permissions based directly on Member.xlsx."""
    allow_self = getattr(config.roles, "allow_self_approval", True) if allow_client is None else allow_client

    member = get_member_by_id_or_name(user_id=user_id, display_name=display_name)
    if member:
        role = (member.get("role") or "").upper()
        can_approve = bool(member.get("can_approve"))
        if role == "PM":
            return True
        if role == "CLIENT":
            return bool(allow_self and can_approve)
        if can_approve:
            return True

    # Check fallback configured GUIDs
    u_id_clean = (user_id or "").lower().strip()
    if u_id_clean:
        if config.roles.pm and config.roles.pm.lower().strip() == u_id_clean:
            return True
        if allow_self and config.roles.client and config.roles.client.lower().strip() == u_id_clean:
            return True

    return False


def is_pm_approval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM or authorized approver from Member.xlsx reacted specifically with
    either 'Admission tickets' (🎟️) or 'Ticket' (🎫) to approve Jira ticket creation.
    """
    if not reactions:
        return False

    for r in reactions:
        u_id = (r.get("userId") or "").strip()
        disp_name = (r.get("displayName") or "").strip()
        r_type = (r.get("reactionType") or "").strip()

        is_authorized = is_user_authorized_approver(user_id=u_id, display_name=disp_name, allow_client=allow_client)
        is_approval = is_ticket_approval_reaction(r_type)

        if is_authorized and is_approval:
            return True
    return False


def is_pm_confirmation_approval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM or authorized approver from Member.xlsx reacted to approve in Step 2
    (supports 🎟️, 🎫, and instant quick-reaction 👍).
    """
    if not reactions:
        return False

    for r in reactions:
        u_id = (r.get("userId") or "").strip()
        disp_name = (r.get("displayName") or "").strip()
        r_type = (r.get("reactionType") or "").strip().lower()

        is_authorized = is_user_authorized_approver(user_id=u_id, display_name=disp_name, allow_client=allow_client)
        is_approval = is_ticket_approval_reaction(r_type) or r_type in {"like", "👍"}

        if is_authorized and is_approval:
            return True
    return False


TICKET_DISAPPROVAL_NAMES = {
    "cross",
    "red x",
    "cross mark",
    "x",
    ":x:",
    ":cross_mark:",
    "cancel",
    "decline",
    "disapprove",
    "thumbsdown",
    "thumbs down",
    ":thumbsdown:",
    "-1",
    ":-1:",
}


def is_ticket_disapproval_reaction(reaction_type: Optional[str]) -> bool:
    """Check if reaction is specifically a decline/disapproval emoji (❌, ✖️, 🚫, 👎)."""
    if not reaction_type:
        return False
    raw = str(reaction_type).strip()
    lower = raw.lower()

    if any(e in raw for e in ["❌", "✖️", "✖", "🚫", "👎"]):
        return True

    cleaned = lower.replace("-", " ").replace("_", " ").strip(": ")
    if cleaned in {"x", "cross", "cross mark", "cancel", "decline", "disapprove", "thumbsdown", "thumbs down", "no"}:
        return True

    for name in TICKET_DISAPPROVAL_NAMES:
        if lower == name or f":{name}:" == lower:
            return True

    return False


def is_pm_disapproval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM or authorized approver from Member.xlsx reacted with a disapproval emoji (❌, 👎)."""
    if not reactions:
        return False

    for r in reactions:
        u_id = (r.get("userId") or "").strip()
        disp_name = (r.get("displayName") or "").strip()
        r_type = (r.get("reactionType") or "").strip()

        is_authorized = is_user_authorized_approver(user_id=u_id, display_name=disp_name, allow_client=allow_client)

        if is_authorized and is_ticket_disapproval_reaction(r_type):
            return True
    return False
