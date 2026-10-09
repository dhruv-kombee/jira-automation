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
    """Check if any TL (Level 1), PM (Level 2), HM (Level 3), or authorized reviewer has added a reaction."""
    if not reactions_raw:
        return False
    try:
        reactions = json.loads(reactions_raw) if isinstance(reactions_raw, str) else reactions_raw
        if not isinstance(reactions, list) or len(reactions) == 0:
            return False

        for r in reactions:
            uid = (r.get("userId") or "").lower().strip()
            dname = (r.get("displayName") or "").lower().strip()

            if pm_user_id and uid == pm_user_id.lower().strip():
                return True
            if pm_name and (pm_name.lower().strip() in dname or dname in pm_name.lower().strip()):
                return True

            # Check if reactor is TL, PM, HM, or authorized management in Member.xlsx
            member = get_member_by_id_or_name(user_id=uid, display_name=dname)
            if member:
                m_role = (member.get("role") or "").upper().strip()
                m_lvl = str(member.get("level") or "").upper().strip()
                if member.get("is_active", True) and (m_role in ("PM", "TL", "HM") or m_lvl in ("LEVEL 1", "LEVEL 2", "LEVEL 3", "1", "2", "3")):
                    return True
                if member.get("can_approve") and m_role not in ("DEVELOPER", "UNASSIGNED", "CLIENT"):
                    return True

            if config.roles.pm and uid == config.roles.pm.lower().strip():
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
        return True, "Management (TL/PM/HM) has already reacted"
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


async def send_stage1_teams_followup_tl(
    msg: Dict[str, Any],
    elapsed_minutes: int,
) -> Dict[str, Any]:
    """Execute Stage 1 (10-minute): Dispatch compact Teams follow-up card tagging Level 1 (TL)."""
    from src.services.member_sync_service import get_active_tls, get_active_pms
    tls = get_active_tls()
    target_users = tls if tls else get_active_pms()
    target_role = "TL" if tls else "PM"

    names = ", ".join(u.get("display_name", "") for u in target_users) or "Team Lead"
    msg_id = msg["message_id"]

    logger.info(
        f"⏰ Triggering 10-minute Level 1 ({target_role}) Teams follow-up for message {msg_id} (recipients: {names})",
        extra={"event": "TL_FOLLOWUP_TRIGGER", "stage": "TEAMS_10M_TL", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

    teams_res = await send_pm_followup_reminder(
        message_id=msg_id,
        pm_name=names,
        pm_user_id=target_users[0].get("user_id") if target_users else None,
        reporter=msg.get("sender_display_name") or "Client",
        elapsed_minutes=elapsed_minutes,
        issues=extract_issues_for_message(msg),
        raw_message=msg.get("message_text") or "",
        chat_id=msg.get("chat_id"),
        team_id=msg.get("team_id"),
        channel_id=msg.get("channel_id"),
        target_role=target_role,
        mention_users=target_users,
    )

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    t_status = "SENT" if teams_res.get("success") else "FAILED"

    db.execute(
        """
        UPDATE messages
        SET reminder_sent_at = ?,
            reminder_count = 1,
            reminder_channel_status = ?
        WHERE message_id = ?
        """,
        (now_iso, f"TL_{t_status}", msg_id),
    )

    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "stage": "TEAMS_10M_TL",
            "messageId": msg_id,
            "reminderSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "targetRole": target_role,
            "names": names,
            "teamsStatus": t_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "stage": "TEAMS_10M_TL",
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "role": target_role,
        "teams_delivery": teams_res,
    }


async def send_stage2_teams_followup_pm(
    msg: Dict[str, Any],
    elapsed_minutes: int,
) -> Dict[str, Any]:
    """Execute Stage 2 (20-minute): Dispatch compact Teams follow-up card tagging Level 2 (PM)."""
    from src.services.member_sync_service import get_active_pms
    pms = get_active_pms()
    names = ", ".join(u.get("display_name", "") for u in pms) or "Project Manager"
    msg_id = msg["message_id"]

    logger.info(
        f"🚨 Triggering 20-minute Level 2 (PM) Teams escalation for message {msg_id} (recipients: {names})",
        extra={"event": "PM_FOLLOWUP_TRIGGER", "stage": "TEAMS_20M_PM", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

    teams_res = await send_pm_followup_reminder(
        message_id=msg_id,
        pm_name=names,
        pm_user_id=pms[0].get("user_id") if pms else None,
        reporter=msg.get("sender_display_name") or "Client",
        elapsed_minutes=elapsed_minutes,
        issues=extract_issues_for_message(msg),
        raw_message=msg.get("message_text") or "",
        chat_id=msg.get("chat_id"),
        team_id=msg.get("team_id"),
        channel_id=msg.get("channel_id"),
        target_role="PM",
        mention_users=pms,
    )

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    t_status = "SENT" if teams_res.get("success") else "FAILED"

    db.execute(
        """
        UPDATE messages
        SET reminder_count = 2,
            reminder_channel_status = ?
        WHERE message_id = ?
        """,
        (f"PM_{t_status}", msg_id),
    )

    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "stage": "TEAMS_20M_PM",
            "messageId": msg_id,
            "reminderSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "targetRole": "PM",
            "names": names,
            "teamsStatus": t_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "stage": "TEAMS_20M_PM",
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "role": "PM",
        "teams_delivery": teams_res,
    }


async def send_stage3_email_escalation_hm(
    msg: Dict[str, Any],
    elapsed_minutes: int,
) -> Dict[str, Any]:
    """Execute Stage 3 (>20-minute): Dispatch urgent escalation email strictly to Level 3 (HM) members."""
    from src.services.member_sync_service import get_active_hms, get_active_pms
    hms = get_active_hms()
    recipients = hms if hms else get_active_pms()
    names = ", ".join(u.get("display_name", "") for u in recipients) or "Higher Management"
    msg_id = msg["message_id"]

    logger.info(
        f"📧 Triggering Level 3 (HM) Email alert for message {msg_id} (unaddressed after 20m; recipients: {names})",
        extra={"event": "HM_EMAIL_TRIGGER", "stage": "EMAIL_HM", "messageId": msg_id, "elapsedMinutes": elapsed_minutes},
    )

    results = []
    for r in recipients:
        r_email = r.get("email")
        if not r_email:
            continue
        res = await send_pm_followup_email(
            pm_email=r_email,
            pm_name=r.get("display_name") or "Management",
            reporter_name=msg.get("sender_display_name") or "Client",
            elapsed_minutes=elapsed_minutes,
            issues=extract_issues_for_message(msg),
            raw_message=msg.get("message_text") or "",
            message_id=msg_id,
            created_at_str=msg.get("created_at") or msg.get("received_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
            teams_web_url=msg.get("message_url"),
        )
        results.append(res)

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    any_success = any(res.get("success") for res in results)
    e_status = "SENT" if any_success else ("FAILED" if results else "NO_HM_EMAIL")

    db.execute(
        """
        UPDATE messages
        SET reminder_email_status = ?,
            reminder_email_sent_at = ?
        WHERE message_id = ?
        """,
        (f"HM_{e_status}", now_iso, msg_id),
    )

    try:
        from src.services.broadcaster import broadcast_message
        await broadcast_message({
            "type": "PM_REMINDER_SENT",
            "stage": "EMAIL_HM",
            "messageId": msg_id,
            "emailSentAt": now_iso,
            "elapsedMinutes": elapsed_minutes,
            "recipients": names,
            "emailStatus": e_status,
        })
    except Exception:
        pass

    return {
        "success": True,
        "stage": "EMAIL_HM",
        "message_id": msg_id,
        "elapsed_minutes": elapsed_minutes,
        "recipients": names,
        "results": results,
    }


async def check_and_send_message_reminder(
    msg_row: sqlite3.Row,
    force: bool = False,
    pm_override: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Inspect a message and execute SLA escalation along the verified 20-minute timeline:
    - At >= 10m: Send compact Teams follow-up card tagging Level 1 (TL).
    - At >= 20m: Send compact Teams follow-up card tagging Level 2 (PM).
    - After 20m unaddressed: Shoot escalation email strictly to Level 3 (HM) members.
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
            # If reminders were started, cancel any pending email
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

    elapsed_minutes = 20
    if msg_dt:
        elapsed = (now_utc - msg_dt).total_seconds() / 60.0
        elapsed_minutes = max(1, int(elapsed))

        # Guard: suppress ancient backlog (>24h)
        if not force and elapsed > 1440:
            if msg.get("reminder_email_status") is None:
                db = get_db()
                db.execute("UPDATE messages SET reminder_email_status = 'SUPPRESSED_BACKLOG' WHERE message_id = ?", (msg_id,))
            return {"skipped": True, "reason": "Message is older than 24 hours (ancient backlog suppressed)"}

    # Verified SLA timeline: 10m TL -> 20m PM -> HM Email
    STAGE1_TL_TIMEOUT = 10
    STAGE2_PM_TIMEOUT = 20

    try:
        reminder_count = int(msg.get("reminder_count") or 0)
    except (ValueError, TypeError):
        reminder_count = 0
    has_sent_stage1 = bool(msg.get("reminder_sent_at"))

    # STAGE 1: 10 minutes -> Level 1 (TL) Teams reminder
    if not has_sent_stage1:
        if not force and elapsed_minutes < STAGE1_TL_TIMEOUT:
            return {
                "skipped": True,
                "reason": f"Under Stage 1 TL follow-up threshold ({elapsed_minutes}m < {STAGE1_TL_TIMEOUT}m)",
            }
        return await send_stage1_teams_followup_tl(msg, elapsed_minutes)

    # STAGE 2: 20 minutes -> Level 2 (PM) Teams escalation
    if reminder_count < 2:
        if not force and elapsed_minutes < STAGE2_PM_TIMEOUT:
            return {
                "skipped": True,
                "reason": f"Under Stage 2 PM escalation threshold ({elapsed_minutes}m < {STAGE2_PM_TIMEOUT}m)",
            }
        return await send_stage2_teams_followup_pm(msg, elapsed_minutes)

    # STAGE 3: >20 minutes (both TL and PM unreplied) -> Level 3 (HM) Email alert only
    has_sent_email = msg.get("reminder_email_status") in ("SENT", "HM_SENT", "CANCELLED", "SUPPRESSED_BACKLOG")

    if not has_sent_email:
        return await send_stage3_email_escalation_hm(msg, elapsed_minutes)

    return {"skipped": True, "reason": "All 3 SLA escalation stages have already been executed"}

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
