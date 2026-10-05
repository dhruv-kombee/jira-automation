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


def is_pm_approval(reactions: Optional[list], allow_client: Optional[bool] = None) -> bool:
    """Check if PM (Santosh Yadav) or authorized approver reacted with an approval emoji (like, thumbsup, heart)."""
    if not reactions:
        return False
    pm_id = (config.roles.pm or "").lower().strip()
    client_id = (config.roles.client or "").lower().strip()
    allow_self = getattr(config.roles, "allow_self_approval", True) if allow_client is None else allow_client

    for r in reactions:
        u_id = (r.get("userId") or "").lower().strip()
        disp_name = (r.get("displayName") or "").lower().strip()
        r_type = (r.get("reactionType") or "").lower().strip()

        # Is reaction from PM? Matches configured PM GUID or name containing "santosh"
        is_pm = (bool(pm_id) and u_id == pm_id) or ("santosh" in disp_name)
        # If self-approval / single-user mode is enabled, client (Dhruv) emoji acts as PM approval
        is_client = (bool(client_id) and u_id == client_id) or ("dhruv" in disp_name)
        is_authorized = is_pm or (allow_self and is_client)

        # Is reaction positive/approval? Strictly thumbs-up, like, or heart
        is_approval = any(pos in r_type for pos in ["like", "👍", "heart", "thumbsup", "❤️"])

        if is_authorized and is_approval:
            return True
    return False

