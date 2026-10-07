import pytest
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from src.database import get_db, init_database
from src.config import config
from src.services.reminder_service import (
    check_and_send_message_reminder,
    check_unanswered_client_issues,
    has_message_reply,
    is_issue_handled_or_replied,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Setup isolated test database for SLA reminder timeline testing."""
    test_db = tmp_path / "test_reminder.db"
    monkeypatch.setattr(config, "database_path", str(test_db))
    monkeypatch.setattr(config, "pm_followup_timeout_minutes", 10)
    monkeypatch.setattr(config, "pm_email_timeout_minutes", 15)
    db = init_database(str(test_db))
    yield db


def _insert_test_message(
    db: sqlite3.Connection,
    message_id: str,
    minutes_ago: float,
    reminder_sent_at: str = None,
    reminder_email_status: str = None,
    reactions: list = None,
    jira_key: str = None,
    text: str = "#issue Payment gateway fails with timeout error",
):
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    iso_ts = dt.isoformat()
    reactions_json = json.dumps(reactions) if reactions else None
    ai_ticket_json = json.dumps({
        "is_ticket_request": True,
        "issues": [{
            "summary": "Payment gateway timeout",
            "issue_type": "Bug",
            "priority": "High",
            "affected_module": "Payments",
        }]
    })

    db.execute(
        """
        INSERT INTO messages (
            message_id, sender_user_id, sender_display_name,
            message_text, ai_ticket, reactions, jira_issue_key,
            created_at, received_at, reminder_sent_at, reminder_email_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id, "client-001", "Client User",
            text, ai_ticket_json, reactions_json, jira_key,
            iso_ts, iso_ts, reminder_sent_at, reminder_email_status,
        ),
    )


@pytest.mark.asyncio
async def test_timeline_stage0_under_10_minutes_is_skipped():
    """Messages less than 10 minutes old should not trigger any reminder."""
    db = get_db()
    _insert_test_message(db, "msg-under-10", minutes_ago=6.0)
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-under-10'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_reminder", new_callable=AsyncMock) as mock_teams, \
         patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        res = await check_and_send_message_reminder(row)

        assert res.get("skipped") is True
        assert "Under follow-up threshold" in res.get("reason", "")
        mock_teams.assert_not_called()
        mock_email.assert_not_called()


@pytest.mark.asyncio
async def test_timeline_stage1_at_10_minutes_sends_teams_followup_only():
    """At 10 minutes, Stage 1 dispatches Teams follow-up card only, and DOES NOT send email."""
    db = get_db()
    _insert_test_message(db, "msg-stage1-10m", minutes_ago=10.5)
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-stage1-10m'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_reminder", new_callable=AsyncMock) as mock_teams, \
         patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        mock_teams.return_value = {"success": True, "method": "chat_quote_reply"}

        res = await check_and_send_message_reminder(row)

        assert res.get("success") is True
        assert res.get("stage") == "TEAMS_FOLLOWUP"
        mock_teams.assert_called_once()
        mock_email.assert_not_called()

        # Verify SQLite record updated with reminder_sent_at
        updated = db.execute("SELECT * FROM messages WHERE message_id = 'msg-stage1-10m'").fetchone()
        assert updated["reminder_sent_at"] is not None
        assert updated["reminder_channel_status"] == "SENT"
        assert updated["reminder_email_status"] is None


@pytest.mark.asyncio
async def test_timeline_waiting_5m_window_after_teams_followup():
    """Between 10m and 15m (e.g. 2m after Teams follow-up), email is withheld during the 5m grace window."""
    db = get_db()
    followup_ts = (datetime.now(timezone.utc) - timedelta(minutes=2.0)).isoformat()
    _insert_test_message(db, "msg-waiting-5m", minutes_ago=12.0, reminder_sent_at=followup_ts)
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-waiting-5m'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        res = await check_and_send_message_reminder(row)

        assert res.get("skipped") is True
        assert "Waiting for 5m reply window" in res.get("reason", "")
        mock_email.assert_not_called()


@pytest.mark.asyncio
async def test_timeline_reply_in_teams_cancels_email_escalation():
    """If someone replies to the message in Teams during the 5m window, email escalation is cancelled."""
    db = get_db()
    followup_ts = (datetime.now(timezone.utc) - timedelta(minutes=6.0)).isoformat()
    _insert_test_message(db, "msg-with-reply", minutes_ago=16.0, reminder_sent_at=followup_ts)

    # Insert a human reply in the thread
    db.execute(
        """
        INSERT INTO messages (message_id, reply_to_id, sender_user_id, sender_display_name, message_text)
        VALUES ('reply-001', 'msg-with-reply', 'pm-user', 'Project Manager', 'Looking into this issue right now')
        """
    )
    assert has_message_reply("msg-with-reply") is True

    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-with-reply'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        res = await check_and_send_message_reminder(row)

        assert res.get("skipped") is True
        assert "Message thread has received a reply" in res.get("reason", "")
        mock_email.assert_not_called()

        # Database should mark email status as CANCELLED
        updated = db.execute("SELECT reminder_email_status FROM messages WHERE message_id = 'msg-with-reply'").fetchone()
        assert updated["reminder_email_status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_timeline_reaction_by_pm_cancels_email_escalation():
    """If PM reacts (🎟️) after Stage 1, email escalation is cancelled."""
    db = get_db()
    followup_ts = (datetime.now(timezone.utc) - timedelta(minutes=6.0)).isoformat()
    reactions = [{"reactionType": "🎟️", "userId": "pm-user-id", "displayName": "Project Manager"}]
    _insert_test_message(
        db, "msg-with-reaction", minutes_ago=16.0,
        reminder_sent_at=followup_ts, reactions=reactions
    )
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-with-reaction'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        res = await check_and_send_message_reminder(row, pm_override={"name": "Project Manager", "user_id": "pm-user-id", "email": "pm@example.com"})

        assert res.get("skipped") is True
        assert "PM has already reacted" in res.get("reason", "")
        mock_email.assert_not_called()

        updated = db.execute("SELECT reminder_email_status FROM messages WHERE message_id = 'msg-with-reaction'").fetchone()
        assert updated["reminder_email_status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_timeline_stage2_at_15_minutes_with_no_reply_shoots_email():
    """At 15m (5m after Teams follow-up) without reaction or reply, Stage 2 shoots email escalation."""
    db = get_db()
    followup_ts = (datetime.now(timezone.utc) - timedelta(minutes=5.5)).isoformat()
    _insert_test_message(db, "msg-shoot-email", minutes_ago=15.5, reminder_sent_at=followup_ts)
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-shoot-email'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = {"success": True, "method": "smtp", "recipients": ["pm@example.com"]}

        res = await check_and_send_message_reminder(row)

        assert res.get("success") is True
        assert res.get("stage") == "EMAIL_ESCALATION"
        mock_email.assert_called_once()

        updated = db.execute("SELECT * FROM messages WHERE message_id = 'msg-shoot-email'").fetchone()
        assert updated["reminder_email_status"] == "SENT"
        assert updated["reminder_email_sent_at"] is not None


@pytest.mark.asyncio
async def test_timeline_both_completed_is_skipped():
    """Once both Teams follow-up and Email escalation are sent, message is fully completed and skipped."""
    db = get_db()
    followup_ts = (datetime.now(timezone.utc) - timedelta(minutes=20.0)).isoformat()
    _insert_test_message(
        db, "msg-completed", minutes_ago=25.0,
        reminder_sent_at=followup_ts, reminder_email_status="SENT"
    )
    row = db.execute("SELECT * FROM messages WHERE message_id = 'msg-completed'").fetchone()

    with patch("src.services.reminder_service.send_pm_followup_reminder", new_callable=AsyncMock) as mock_teams, \
         patch("src.services.reminder_service.send_pm_followup_email", new_callable=AsyncMock) as mock_email:
        res = await check_and_send_message_reminder(row)

        assert res.get("skipped") is True
        assert "Both Teams follow-up and Email escalation already completed" in res.get("reason", "")
        mock_teams.assert_not_called()
        mock_email.assert_not_called()
