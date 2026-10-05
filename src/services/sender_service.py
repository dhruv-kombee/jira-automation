from typing import Optional
from src.config import config
from src.logger import logger


class Roles:
    CLIENT = "CLIENT"
    PM = "PM"
    DEVELOPER = "DEVELOPER"
    UNKNOWN = "UNKNOWN"


def identify_sender_role(user_id: Optional[str] = None, display_name: Optional[str] = None) -> str:
    """Identify the role of a user by Microsoft Graph user ID, falling back to display name.

    Matches against configured environment variables or known team member names.
    """
    # 1. Match by configured GUID
    if user_id:
        normalized = user_id.strip().lower()
        if config.roles.client and config.roles.client.strip().lower() == normalized:
            return Roles.CLIENT
        if config.roles.pm and config.roles.pm.strip().lower() == normalized:
            return Roles.PM
        if config.roles.developer and config.roles.developer.strip().lower() == normalized:
            return Roles.DEVELOPER

    # 2. Match by display name (Dhruv -> Client, Santosh -> PM, Musaib -> Developer)
    if display_name:
        name_lower = display_name.strip().lower()
        if "dhruv" in name_lower:
            return Roles.CLIENT
        if "santosh" in name_lower:
            return Roles.PM
        if "musaib" in name_lower or "musain" in name_lower:
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


def is_pm_approval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM (Santosh Yadav) or authorized approver reacted specifically with
    either 'Admission tickets' (🎟️) or 'Ticket' (🎫) to approve Jira ticket creation.
    """
    if not reactions:
        return False
    pm_id = (config.roles.pm or "").lower().strip()
    client_id = (config.roles.client or "").lower().strip()
    allow_self = getattr(config.roles, "allow_self_approval", True) if allow_client is None else allow_client

    for r in reactions:
        u_id = (r.get("userId") or "").lower().strip()
        disp_name = (r.get("displayName") or "").lower().strip()
        r_type = (r.get("reactionType") or "").strip()

        # Is reaction from PM? Matches configured PM GUID or name containing "santosh"
        is_pm = (bool(pm_id) and u_id == pm_id) or ("santosh" in disp_name)
        # If self-approval / single-user mode is enabled, client (Dhruv) emoji acts as PM approval
        is_client = (bool(client_id) and u_id == client_id) or ("dhruv" in disp_name)
        is_authorized = is_pm or (allow_self and is_client)

        # Strictly only 'Admission tickets' (🎟️) and 'Ticket' (🎫) approve
        is_approval = is_ticket_approval_reaction(r_type)

        if is_authorized and is_approval:
            return True
    return False


def is_pm_confirmation_approval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM reacted to approve in Step 2 (supports 🎟️, 🎫, and instant quick-reaction 👍)."""
    if not reactions:
        return False
    pm_id = (config.roles.pm or "").lower().strip()
    client_id = (config.roles.client or "").lower().strip()
    allow_self = getattr(config.roles, "allow_self_approval", True) if allow_client is None else allow_client

    for r in reactions:
        u_id = (r.get("userId") or "").lower().strip()
        disp_name = (r.get("displayName") or "").lower().strip()
        r_type = (r.get("reactionType") or "").strip().lower()

        is_pm = (bool(pm_id) and u_id == pm_id) or ("santosh" in disp_name)
        is_client = (bool(client_id) and u_id == client_id) or ("dhruv" in disp_name)
        is_authorized = is_pm or (allow_self and is_client)

        # In Step 2, accept 🎟️, 🎫, as well as instant quick reaction 👍 (like)
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
    """Check if PM or authorized user reacted with a disapproval emoji (❌, 👎)."""
    if not reactions:
        return False
    pm_id = (config.roles.pm or "").lower().strip()
    client_id = (config.roles.client or "").lower().strip()
    allow_self = getattr(config.roles, "allow_self_approval", True) if allow_client is None else allow_client

    for r in reactions:
        u_id = (r.get("userId") or "").lower().strip()
        disp_name = (r.get("displayName") or "").lower().strip()
        r_type = (r.get("reactionType") or "").strip()

        is_pm = (bool(pm_id) and u_id == pm_id) or ("santosh" in disp_name)
        is_client = (bool(client_id) and u_id == client_id) or ("dhruv" in disp_name)
        is_authorized = is_pm or (allow_self and is_client)

        if is_authorized and is_ticket_disapproval_reaction(r_type):
            return True
    return False


