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


def is_pm_approval(reactions: Optional[list]) -> bool:
    """Check if PM (Santosh Yadav) has reacted with an approval emoji (like, thumbsup, heart)."""
    if not reactions:
        return False
    pm_id = (config.roles.pm or "").lower().strip()
    for r in reactions:
        u_id = (r.get("userId") or "").lower().strip()
        disp_name = (r.get("displayName") or "").lower().strip()
        r_type = (r.get("reactionType") or "").lower().strip()

        # Is reaction from PM?
        is_pm = (pm_id and u_id == pm_id) or ("santosh" in disp_name)
        # Is reaction positive/approval?
        is_approval = any(pos in r_type for pos in ["like", "👍", "heart"]) or (r.get("displayName") == "Like")

        if is_pm and is_approval:
            return True
    return False

