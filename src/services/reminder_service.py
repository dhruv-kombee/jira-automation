"""PM Follow-up SLA Reminder Engine.

Monitors incoming client messages that are identified as issues/bugs.
If a Project Manager (PM) has not given any reaction or approval within the
configured SLA window (default: 15 minutes):
  1. Posts an escalated follow-up Adaptive Card to Microsoft Teams tagging the PM (@mention).
  2. Dispatches an urgent, branded notification email to the PM in Outlook.
  3. Records escalation timestamps and broadcasts live status to the operations dashboard.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from src.config import config
from src.database import get_db
from src.logger import logger
from src.services.email_service import send_pm_followup_email
from src.services.teams_notifier import send_pm_followup_reminder


_reminder_task: Optional[asyncio.Task] = None
REMINDER_CHECK_INTERVAL = 30  # Run check every 30 seconds
_MONITOR_START_TIME: datetime = datetime.now(timezone.utc)


def get_active_pm() -> Dict[str, str]:
    """Retrieve active Project Manager identity (name, email, user_id) from database or config."""
    db = get_db()
    # Check team_members table for configured PM
    row = db.execute(
        "SELECT user_id, display_name, email FROM team_members WHERE is_active = 1 AND role = 'PM' ORDER BY id ASC LIMIT 1"
    ).fetchone()

    if row:
        return {
            "name": row["display_name"] or "Santosh Yadav",
            "email": row["email"] or "santosh.yadav@kombee.com",
            "user_id": row["user_id"] or config.roles.pm or "",
        }

    return {
        "name": "Santosh Yadav",
        "email": "santosh.yadav@kombee.com",
        "user_id": config.roles.pm or "d7bc3c28-33d9-4973-816e-445d51556b8b",
    }


def parse_timestamp_to_utc(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse various timestamp formats safely into a UTC datetime object."""
    if not ts_str:
        return None
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        if " " in clean and "+" not in clean and "-" not in clean[10:]:
            # SQLite format: '2026-10-06 09:30:00'
            dt = datetime.fromisoformat(clean)
            return dt.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def has_pm_reacted(reactions_raw: Any, pm_user_id: Optional[str] = None) -> bool:
    """Check if the PM or any authorized reviewer has already added a reaction."""
    if not reactions_raw:
        return False
    try:
        reactions = json.loads(reactions_raw) if isinstance(reactions_raw, str) else reactions_raw
        if not isinstance(reactions, list) or len(reactions) == 0:
            return False

        pm_id_clean = (pm_user_id or config.roles.pm or "").lower().strip()
        client_id_clean = (config.roles.client or "").lower().strip()

        for r in reactions:
            uid = (r.get("userId") or "").lower().strip()
            dname = (r.get("displayName") or "").lower().strip()
            # If PM reacted, or if single-user test allowed client reacted
            if pm_id_clean and uid == pm_id_clean:
                return True
            if "santosh" in dname or "pm" in dname:
                return True
            if config.roles.allow_self_approval and client_id_clean and uid == client_id_clean:
                return True
        return False
    except Exception:
        return False


async def check_and_send_message_reminder(
    msg_row: sqlite3.Row,
    force: bool = False,
    pm_override: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Inspect a message and execute follow-up reminder if SLA has expired without PM reaction."""
    msg = dict(msg_row)
    msg_id = msg.get("message_id")
    if not msg_id:
        return {"success": False, "error": "Invalid message row"}

    # Already resolved with a Jira ticket?
    if msg.get("jira_issue_key") and not force:
        return {"skipped": True, "reason": "Jira ticket already created"}

    # Already declined?
    if msg.get("confirmation_status") == "DECLINED" and not force:
        return {"skipped": True, "reason": "Ticket was declined"}

    # Already sent reminder?
    if msg.get("reminder_sent_at") and not force:
        return {"skipped": True, "reason": "Reminder already sent"}

    pm_info = pm_override or get_active_pm()

    # Check if PM has already reacted
    if not force and has_pm_reacted(msg.get("reactions"), pm_info.get("user_id")):
        return {"skipped": True, "reason": "PM has already reacted"}

    # Calculate elapsed time
    timeout_minutes = config.pm_reminder_timeout_minutes
    ts_str = msg.get("received_at") or msg.get("created_at")
    msg_dt = parse_timestamp_to_utc(ts_str)
    now_utc = datetime.now(timezone.utc)

    elapsed_minutes = 15
    if msg_dt:
        elapsed = (now_utc - msg_dt).total_seconds() / 60.0
        elapsed_minutes = max(1, int(elapsed))
        if elapsed < timeout_minutes and not force:
            return {"skipped": True, "reason": f"Under timeout threshold ({int(elapsed)}m < {timeout_minutes}m)"}

        # Guard: suppress automatic emails/reminders for messages that predate current monitor session
        if not force and msg_dt < _MONITOR_START_TIME:
            return {"skipped": True, "reason": "Message predates active monitor session (historical email suppressed)"}

    # Parse AI ticket / issues
    raw_ai = msg.get("ai_ticket")
    ai_ticket = None
    if raw_ai:
        try:
            ai_ticket = json.loads(raw_ai) if isinstance(raw_ai, str) else raw_ai
        except Exception:
            ai_ticket = None

    raw_issues = []
    if ai_ticket:
        raw_issues = ai_ticket.get("issues") or [ai_ticket]
    if not raw_issues:
        raw_issues = [{
            "summary": msg.get("message_text") or "Client Issue Report",
            "issue_type": config.jira.default_issue_type,
            "priority": "Medium",
            "affected_module": "General",
        }]

    reporter = msg.get("sender_display_name") or "Client"
    raw_text = msg.get("message_text") or ""
    chat_id = msg.get("chat_id")
    team_id = msg.get("team_id")
    channel_id = msg.get("channel_id")
    created_str = msg.get("created_at") or msg.get("received_at") or datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info(
        f"⏰ Triggering 15-minute PM follow-up for message {msg_id} (elapsed: {elapsed_minutes}m)",
        extra={"event": "PM_REMINDER_TRIGGER", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

    # 1. Dispatch Teams Follow-Up Card with PM @mention
    teams_res = await send_pm_followup_reminder(
        message_id=msg_id,
        pm_name=pm_info["name"],
        pm_user_id=pm_info["user_id"],
        reporter=reporter,
        elapsed_minutes=elapsed_minutes,
        issues=raw_issues,
        raw_message=raw_text,
        chat_id=chat_id,
        team_id=team_id,
        channel_id=channel_id,
    )

    # 2. Dispatch Outlook Email to PM
    email_res = await send_pm_followup_email(
        pm_email=pm_info["email"],
        pm_name=pm_info["name"],
        reporter_name=reporter,
        elapsed_minutes=elapsed_minutes,
        issues=raw_issues,
        raw_message=raw_text,
        message_id=msg_id,
        created_at_str=created_str,
        teams_web_url=msg.get("message_url"),
    )

    # 3. Update SQLite database
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    t_status = "SENT" if teams_res.get("success") else "FAILED"
    e_status = "SENT" if email_res.get("success") else "FAILED"

    db.execute(
        """
        UPDATE messages
        SET reminder_sent_at = ?,
            reminder_count = COALESCE(reminder_count, 0) + 1,
            reminder_channel_status = ?,
            reminder_email_status = ?
        WHERE message_id = ?
        """,
        (now_iso, t_status, e_status, msg_id),
    )

    # 4. Broadcast live update to dashboard
    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "messageId": msg_id,
            "reminderSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "pmName": pm_info["name"],
            "pmEmail": pm_info["email"],
            "teamsStatus": t_status,
            "emailStatus": e_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "pm": pm_info,
        "teams_delivery": teams_res,
        "email_delivery": email_res,
    }


async def check_unanswered_client_issues():
    """Poll database for messages waiting for PM reaction that have exceeded SLA threshold."""
    db = get_db()
    timeout_minutes = config.pm_reminder_timeout_minutes

    # Look for candidate messages that have an issue and no Jira ticket
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM messages
        WHERE (ai_ticket IS NOT NULL OR message_text LIKE '%#issue%' OR message_text LIKE '%#bug%')
          AND jira_issue_key IS NULL
          AND (confirmation_status IS NULL OR confirmation_status != 'DECLINED')
          AND reminder_sent_at IS NULL
        ORDER BY id DESC
        LIMIT 25
    """)
    rows = cursor.fetchall()

    for row in rows:
        try:
            await check_and_send_message_reminder(row)
        except Exception as check_err:
            logger.warning(f"Error checking reminder for message {row['message_id']}: {check_err}")


async def reminder_monitor_loop():
    """Background polling loop for SLA reminders."""
    logger.info(
        f"⏰ PM Follow-up SLA monitor started (checks every {REMINDER_CHECK_INTERVAL}s, triggers at >= {config.pm_reminder_timeout_minutes}m)",
        extra={"event": "REMINDER_MONITOR_START"},
    )
    # Give server 5 seconds to complete startup
    await asyncio.sleep(5)

    while True:
        try:
            await check_unanswered_client_issues()
        except asyncio.CancelledError:
            break
        except Exception as loop_err:
            logger.warning(f"Error in SLA reminder monitor loop: {loop_err}")

        await asyncio.sleep(REMINDER_CHECK_INTERVAL)


def start_reminder_monitor():
    """Start the background SLA reminder monitoring task."""
    global _reminder_task
    if _reminder_task is None or _reminder_task.done():
        _reminder_task = asyncio.create_task(reminder_monitor_loop())


def stop_reminder_monitor():
    """Cancel the background SLA reminder monitor task."""
    global _reminder_task
    if _reminder_task and not _reminder_task.done():
        _reminder_task.cancel()
        _reminder_task = None
