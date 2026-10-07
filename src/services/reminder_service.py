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


from src.services.member_sync_service import get_active_pm_from_excel, get_member_by_id_or_name


def get_active_pm() -> Dict[str, str]:
    """Retrieve active Project Manager identity (name, email, user_id) directly from Member.xlsx."""
    return get_active_pm_from_excel()


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


def has_pm_reacted(
    reactions_raw: Any,
    pm_user_id: Optional[str] = None,
    pm_name: Optional[str] = None,
) -> bool:
    """Check if the PM or any authorized reviewer from Member.xlsx has already added a reaction."""
    if not reactions_raw:
        return False
    try:
        reactions = json.loads(reactions_raw) if isinstance(reactions_raw, str) else reactions_raw
        if not isinstance(reactions, list) or len(reactions) == 0:
            return False

        pm_info = get_active_pm()
        pm_uid = (pm_user_id or pm_info.get("user_id") or config.roles.pm or "").lower().strip()
        pm_display = (pm_name or pm_info.get("name") or "").lower().strip()
        client_uid = (config.roles.client or "").lower().strip()

        for r in reactions:
            uid = (r.get("userId") or "").lower().strip()
            dname = (r.get("displayName") or "").lower().strip()

            # Direct match with active PM
            if pm_uid and uid == pm_uid:
                return True
            if pm_display and (pm_display in dname or dname in pm_display):
                return True

            # Check if reactor has approval rights in Member.xlsx
            member = get_member_by_id_or_name(user_id=uid, display_name=dname)
            if member:
                if (member.get("role") or "").upper() == "PM":
                    return True
                if member.get("can_approve"):
                    return True
                if config.roles.allow_self_approval and (member.get("role") or "").upper() == "CLIENT":
                    return True

            if config.roles.allow_self_approval and client_uid and uid == client_uid:
                return True
        return False
    except Exception:
        return False


def has_message_reply(msg_id: str) -> bool:
    """Check if any reply from a non-bot user has been posted to this message in Teams."""
    if not msg_id:
        return False
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT message_text, sender_display_name FROM messages
            WHERE reply_to_id = ?
            ORDER BY id ASC
            """,
            (msg_id,),
        )
        rows = cursor.fetchall()
        for r in rows:
            txt = (r["message_text"] or "").lower()
            # Ignore automated bot reminder replies
            if "please review client issue" in txt and "react" in txt:
                continue
            return True
        return False
    except Exception:
        return False


def is_issue_handled_or_replied(
    msg: Dict[str, Any],
    pm_user_id: Optional[str] = None,
    pm_name: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Determine if an issue has already been resolved, reacted to, or replied to."""
    if msg.get("jira_issue_key"):
        return True, "Jira ticket already created"
    if msg.get("confirmation_status") == "DECLINED":
        return True, "Ticket was declined"
    if has_pm_reacted(msg.get("reactions"), pm_user_id, pm_name):
        return True, "PM has already reacted"
    if has_message_reply(msg.get("message_id")):
        return True, "Message thread has received a reply"
    return False, None


def extract_issues_for_message(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract parsed issues or build fallback single issue from message row."""
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
    return raw_issues


async def send_stage1_teams_followup(
    msg: Dict[str, Any],
    pm_info: Dict[str, str],
    elapsed_minutes: int,
) -> Dict[str, Any]:
    """Execute Stage 1 (10-minute): Dispatch compact Teams follow-up card with PM @mention."""
    msg_id = msg["message_id"]
    raw_issues = extract_issues_for_message(msg)
    reporter = msg.get("sender_display_name") or "Client"
    raw_text = msg.get("message_text") or ""
    chat_id = msg.get("chat_id")
    team_id = msg.get("team_id")
    channel_id = msg.get("channel_id")

    logger.info(
        f"⏰ Triggering 10-minute PM Teams follow-up for message {msg_id} (elapsed: {elapsed_minutes}m)",
        extra={"event": "PM_FOLLOWUP_TRIGGER", "stage": "TEAMS_10M", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

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

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    t_status = "SENT" if teams_res.get("success") else "FAILED"

    db.execute(
        """
        UPDATE messages
        SET reminder_sent_at = ?,
            reminder_count = COALESCE(reminder_count, 0) + 1,
            reminder_channel_status = ?
        WHERE message_id = ?
        """,
        (now_iso, t_status, msg_id),
    )

    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "stage": "TEAMS_FOLLOWUP",
            "messageId": msg_id,
            "reminderSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "pmName": pm_info["name"],
            "pmEmail": pm_info["email"],
            "teamsStatus": t_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "stage": "TEAMS_FOLLOWUP",
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "pm": pm_info,
        "teams_delivery": teams_res,
    }


async def send_stage2_email_escalation(
    msg: Dict[str, Any],
    pm_info: Dict[str, str],
    elapsed_minutes: int,
) -> Dict[str, Any]:
    """Execute Stage 2 (15-minute / +5m after Teams): Shoot Outlook email alert to PM."""
    msg_id = msg["message_id"]
    raw_issues = extract_issues_for_message(msg)
    reporter = msg.get("sender_display_name") or "Client"
    raw_text = msg.get("message_text") or ""
    created_str = msg.get("created_at") or msg.get("received_at") or datetime.now().strftime("%Y-%m-%d %H:%M")

    logger.info(
        f"📧 Triggering 15-minute PM Email escalation for message {msg_id} (elapsed: {elapsed_minutes}m, no reply within 5m after follow-up)",
        extra={"event": "PM_EMAIL_TRIGGER", "stage": "EMAIL_15M", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

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

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    e_status = "SENT" if email_res.get("success") else "FAILED"

    db.execute(
        """
        UPDATE messages
        SET reminder_email_status = ?,
            reminder_email_sent_at = ?
        WHERE message_id = ?
        """,
        (e_status, now_iso, msg_id),
    )

    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "stage": "EMAIL_ESCALATION",
            "messageId": msg_id,
            "emailSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "pmName": pm_info["name"],
            "pmEmail": pm_info["email"],
            "emailStatus": e_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "stage": "EMAIL_ESCALATION",
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "pm": pm_info,
        "email_delivery": email_res,
    }


async def check_and_send_message_reminder(
    msg_row: sqlite3.Row,
    force: bool = False,
    pm_override: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Inspect a message and execute SLA escalation along the timeline:
    - At >= 10m: Send compact follow-up message to Teams tagging PM (@mention).
    - At >= 15m (>= 5m after Teams follow-up): Shoot escalation email if no reaction or reply has occurred.
    """
    msg = dict(msg_row)
    msg_id = msg.get("message_id")
    if not msg_id:
        return {"success": False, "error": "Invalid message row"}

    pm_info = pm_override or get_active_pm()

    # Check if issue is already handled / resolved / replied
    if not force:
        handled, reason = is_issue_handled_or_replied(msg, pm_info.get("user_id"), pm_info.get("name"))
        if handled:
            # If Stage 1 was already sent, cancel any pending Stage 2 email
            if msg.get("reminder_sent_at") and msg.get("reminder_email_status") is None:
                db = get_db()
                db.execute(
                    "UPDATE messages SET reminder_email_status = 'CANCELLED' WHERE message_id = ?",
                    (msg_id,),
                )
            return {"skipped": True, "reason": reason}

    # Calculate elapsed time
    ts_str = msg.get("received_at") or msg.get("created_at")
    msg_dt = parse_timestamp_to_utc(ts_str)
    now_utc = datetime.now(timezone.utc)

    elapsed_minutes = 15
    if msg_dt:
        elapsed = (now_utc - msg_dt).total_seconds() / 60.0
        elapsed_minutes = max(1, int(elapsed))

        # Guard: suppress ancient backlog (>24h)
        if not force and elapsed > 1440:
            if msg.get("reminder_email_status") is None:
                db = get_db()
                db.execute("UPDATE messages SET reminder_email_status = 'SUPPRESSED_BACKLOG' WHERE message_id = ?", (msg_id,))
            return {"skipped": True, "reason": "Message is older than 24 hours (ancient backlog suppressed)"}

    followup_threshold = config.pm_followup_timeout_minutes  # 10 minutes
    email_threshold = config.pm_email_timeout_minutes        # 15 minutes

    # STAGE 1: Has Teams follow-up been sent?
    has_sent_followup = bool(msg.get("reminder_sent_at"))

    if not has_sent_followup:
        # Check if 10m threshold reached
        if not force and elapsed_minutes < followup_threshold:
            return {
                "skipped": True,
                "reason": f"Under follow-up threshold ({elapsed_minutes}m < {followup_threshold}m)",
            }
        # Fire Stage 1 (Teams follow-up)
        return await send_stage1_teams_followup(msg, pm_info, elapsed_minutes)

    # STAGE 2: Has Email escalation been sent or cancelled?
    has_sent_email = msg.get("reminder_email_status") in ("SENT", "CANCELLED", "SUPPRESSED_BACKLOG")

    if not has_sent_email:
        # Calculate time since Stage 1 follow-up was dispatched
        followup_dt = parse_timestamp_to_utc(msg.get("reminder_sent_at"))
        since_followup_min = (now_utc - followup_dt).total_seconds() / 60.0 if followup_dt else 5.0

        # Check if 15m total (and >= 5m since Teams follow-up) has elapsed
        if not force:
            if elapsed_minutes < email_threshold and since_followup_min < 5.0:
                return {
                    "skipped": True,
                    "reason": f"Waiting for 5m reply window after Teams follow-up ({int(since_followup_min)}m < 5m, total {elapsed_minutes}m < {email_threshold}m)",
                }
        # Fire Stage 2 (Email escalation)
        return await send_stage2_email_escalation(msg, pm_info, elapsed_minutes)

    return {"skipped": True, "reason": "Both Teams follow-up and Email escalation already completed"}


async def check_unanswered_client_issues():
    """Poll database for messages waiting for PM reaction that have exceeded SLA threshold."""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM messages
        WHERE (ai_ticket IS NOT NULL OR message_text LIKE '%#issue%' OR message_text LIKE '%#bug%')
          AND jira_issue_key IS NULL
          AND (confirmation_status IS NULL OR confirmation_status != 'DECLINED')
          AND (
              reminder_sent_at IS NULL
              OR reminder_email_status IS NULL
              OR reminder_email_status NOT IN ('SENT', 'CANCELLED', 'SUPPRESSED_BACKLOG')
          )
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
        f"⏰ PM Follow-up SLA monitor started (checks every {REMINDER_CHECK_INTERVAL}s: 10m Teams follow-up, 15m Email escalation)",
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
