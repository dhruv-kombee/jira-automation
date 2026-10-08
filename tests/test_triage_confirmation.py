import pytest
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock
from src.services.sender_service import (
    is_ticket_approval_reaction,
    is_ticket_disapproval_reaction,
    is_pm_approval,
    is_pm_disapproval,
    is_pm_confirmation_approval,
)
from src.database import get_db, init_database
from src.repositories.message_repository import store_message
from src.services.message_service import (
    check_and_auto_create_jira_ticket,
    execute_jira_ticket_creation,
    execute_jira_ticket_decline,
)


def test_reaction_identification():
    # Ticket approval emoji and variants
    assert is_ticket_approval_reaction("🎟️") is True
    assert is_ticket_approval_reaction("🎟") is True
    assert is_ticket_approval_reaction("🎫") is True
    assert is_ticket_approval_reaction("admission ticket") is True
    assert is_ticket_approval_reaction("ticket") is True
    assert is_ticket_approval_reaction(":admission_tickets:") is True

    # Non-approval reactions
    assert is_ticket_approval_reaction("👍") is False
    assert is_ticket_approval_reaction("❤️") is False
    assert is_ticket_approval_reaction("laugh") is False

    # Disapproval reactions
    assert is_ticket_disapproval_reaction("❌") is True
    assert is_ticket_disapproval_reaction("✖️") is True
    assert is_ticket_disapproval_reaction("🚫") is True
    assert is_ticket_disapproval_reaction("👎") is True
    assert is_ticket_disapproval_reaction("decline") is True
    assert is_ticket_disapproval_reaction("cancel") is True


def test_pm_reaction_authorization():
    # PM approval with ticket emoji
    pm_reactions = [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "🎟️"}]
    assert is_pm_approval(pm_reactions) is True
    assert is_pm_disapproval(pm_reactions) is False

    # PM disapproval with cross emoji
    pm_disapprove = [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "❌"}]
    assert is_pm_disapproval(pm_disapprove) is True
    assert is_pm_approval(pm_disapprove) is False

    # PM Step 2 approval with default quick reaction 👍
    pm_thumbs_up = [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "like"}]
    assert is_pm_confirmation_approval(pm_thumbs_up) is True
    assert is_pm_approval(pm_thumbs_up) is False  # Step 1 strictly requires ticket emoji

    # Non-authorized user reaction with ticket emoji should NOT approve if allow_self is False
    dev_reactions = [{"userId": "dev-1", "displayName": "Santosh Yadav", "reactionType": "🎟️"}]
    assert is_pm_approval(dev_reactions, allow_client=False) is False


@pytest.mark.asyncio
async def test_two_step_triage_flow_approval(tmp_path):
    # Initialize isolated database
    test_db = tmp_path / "test_triage.db"
    init_database(str(test_db))

    msg_id = "msg-triage-approve-1"
    normalized_msg = {
        "messageId": msg_id,
        "chatId": "chat-123",
        "sender": {"userId": "client-1", "displayName": "Dhruv dobariya"},
        "message": {"text": "Navbar search bar produces 404 error when clicking enter", "createdAt": "2026-10-05T12:00:00Z"},
        "reactions": [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "🎟️"}],
    }
    store_message(normalized_msg)

    mock_ai_ticket = {
        "is_ticket_request": True,
        "summary": "Navbar search bar returns 404 on enter",
        "description": "User reported 404 error on search submission",
        "issue_type": "Bug",
        "priority": "High",
        "affected_module": "Frontend/Search",
        "suggested_assignee": "Musaib Khan",
        "observed_behavior": "404 error",
        "issues": [{
            "summary": "Navbar search bar returns 404 on enter",
            "issue_type": "Bug",
            "priority": "High",
            "affected_module": "Frontend/Search",
            "suggested_assignee": "Musaib Khan",
        }],
    }

    # STEP 1: PM reacts with 🎟️ on client message
    with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
         patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=mock_ai_ticket)), \
         patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})) as mock_pending_notify, \
         patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create_jira:

        step1_res = await check_and_auto_create_jira_ticket(normalized_msg, "CLIENT")

        # In Step 1, Jira ticket must NOT be created yet!
        mock_create_jira.assert_not_called()
        # The pending confirmation card MUST be sent to Teams
        mock_pending_notify.assert_called_once()
        assert step1_res is not None
        assert step1_res.get("status") == "AWAITING_FINAL_CONFIRMATION"

        # Check DB status
        row = get_db().execute("SELECT confirmation_status, jira_issue_key FROM messages WHERE message_id = ?", (msg_id,)).fetchone()
        assert row["confirmation_status"] == "AWAITING_FINAL_CONFIRMATION"
        assert row["jira_issue_key"] is None

    # STEP 2: PM confirms approval (reacts with 🎟️ again or clicks 1-click confirmation)
    with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
         patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-99", "url": "https://test.atlassian.net/browse/SCRUM-99", "summary": "Navbar search bar returns 404 on enter"})) as mock_create_jira_2, \
         patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})) as mock_created_notify:

        step2_res = await check_and_auto_create_jira_ticket(normalized_msg, "CLIENT")

        # Now Jira ticket MUST be created!
        mock_create_jira_2.assert_called_once()
        mock_created_notify.assert_called_once()
        assert step2_res is not None
        assert step2_res.get("key") == "SCRUM-99"

        # Check DB status
        row = get_db().execute("SELECT confirmation_status, jira_issue_key FROM messages WHERE message_id = ?", (msg_id,)).fetchone()
        assert row["confirmation_status"] == "APPROVED"
        assert row["jira_issue_key"] == "SCRUM-99"


@pytest.mark.asyncio
async def test_two_step_triage_flow_disapproval(tmp_path):
    # Initialize isolated database
    test_db = tmp_path / "test_decline.db"
    init_database(str(test_db))

    msg_id = "msg-triage-decline-1"
    normalized_msg = {
        "messageId": msg_id,
        "chatId": "chat-123",
        "sender": {"userId": "client-1", "displayName": "Dhruv dobariya"},
        "message": {"text": "Please check if database is running slow", "createdAt": "2026-10-05T12:00:00Z"},
        "reactions": [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "🎟️"}],
    }
    store_message(normalized_msg)

    mock_ai_ticket = {
        "is_ticket_request": True,
        "summary": "Database performance inquiry",
        "description": "User asking about db performance",
        "issue_type": "Task",
        "priority": "Medium",
        "affected_module": "Backend/DB",
        "suggested_assignee": "Musaib Khan",
        "issues": [{"summary": "Database performance inquiry", "issue_type": "Task", "priority": "Medium"}],
    }

    # Step 1: Send confirmation card
    with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
         patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=mock_ai_ticket)), \
         patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
        await check_and_auto_create_jira_ticket(normalized_msg, "CLIENT")

    # Step 2: PM declines via ❌ reaction
    normalized_msg["reactions"] = [{"userId": "pm-1", "displayName": "Hemil Ghori", "reactionType": "❌"}]

    with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
         patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create_jira, \
         patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})) as mock_declined_notify:

        decline_res = await check_and_auto_create_jira_ticket(normalized_msg, "CLIENT")

        # Zero Jira tickets must be created
        mock_create_jira.assert_not_called()
        mock_declined_notify.assert_called_once()
        assert decline_res.get("status") == "DECLINED"

        # Check DB status
        row = get_db().execute("SELECT confirmation_status, jira_issue_key FROM messages WHERE message_id = ?", (msg_id,)).fetchone()
        assert row["confirmation_status"] == "DECLINED"
        assert row["jira_issue_key"] is None


@pytest.mark.asyncio
async def test_non_issue_message_ignored(tmp_path):
    test_db = tmp_path / "test_non_issue.db"
    init_database(str(test_db))

    msg_id = "msg-greeting-1"
    normalized_msg = {
        "messageId": msg_id,
        "chatId": "chat-123",
        "sender": {"userId": "client-1", "displayName": "Dhruv dobariya"},
        "message": {"text": "Good morning team!", "createdAt": "2026-10-05T12:00:00Z"},
        "reactions": [{"userId": "pm-1", "displayName": "Santosh Yadav", "reactionType": "🎟️"}],
    }
    store_message(normalized_msg)

    mock_ai_ticket = {
        "is_ticket_request": False,
        "summary": "Non-ticket conversation",
        "description": "General greeting",
    }

    with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
         patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=mock_ai_ticket)), \
         patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock()) as mock_pending_notify, \
         patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create_jira:

        res = await check_and_auto_create_jira_ticket(normalized_msg, "CLIENT")
        assert res is None
        mock_pending_notify.assert_not_called()
        mock_create_jira.assert_not_called()
