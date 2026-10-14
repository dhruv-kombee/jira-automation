import pytest
import sqlite3
import json
from unittest.mock import AsyncMock, patch, MagicMock

from src.database import get_db, init_database
from src.repositories.message_repository import store_message
from src.services.message_service import (
    is_bot_or_ticket_created_message,
    check_and_auto_create_jira_ticket,
    process_teams_message,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Set up an isolated test SQLite database using init_database."""
    test_db = tmp_path / "test_dup_prev.db"
    conn = init_database(str(test_db))
    yield conn


def test_is_bot_or_ticket_created_message_detection():
    # 1. Ticket Created card text
    ticket_msg = {
        "messageId": "msg-101",
        "sender": {"userId": "bot-1", "displayName": "Jira Bot"},
        "message": {"text": "🎟️ Ticket Created: SCRUM-118\nTopic Details: Broken footer\nScrum Link: https://jira/SCRUM-118"},
        "attachments": [],
    }
    assert is_bot_or_ticket_created_message(ticket_msg) is True

    # 2. HTML notification message with ticket link
    html_msg = {
        "messageId": "msg-102",
        "sender": {"userId": "bot-1", "displayName": "Jira Automation"},
        "message": {"text": "🎟️ Ticket Created\nTopic Details: Navbar bug\nScrum Link: SCRUM-119"},
        "attachments": [],
    }
    assert is_bot_or_ticket_created_message(html_msg) is True

    # 3. Pure Adaptive Card payload with no user text
    card_msg = {
        "messageId": "msg-103",
        "sender": {"userId": None, "displayName": None},
        "message": {"text": ""},
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive"}],
    }
    assert is_bot_or_ticket_created_message(card_msg) is True

    # 4. Message that already has Jira issue key
    jira_assigned_msg = {
        "messageId": "msg-104",
        "jira_issue_key": "SCRUM-120",
        "message": {"text": "issue found in dashboard"},
    }
    assert is_bot_or_ticket_created_message(jira_assigned_msg) is True

    # 5. Normal client message - MUST NOT be flagged as bot/ticket
    client_msg = {
        "messageId": "msg-105",
        "sender": {"userId": "client-1", "displayName": "Nishi Sharma"},
        "message": {"text": "issue found in the footer of the dashboard ."},
        "attachments": [],
    }
    assert is_bot_or_ticket_created_message(client_msg) is False


@pytest.mark.anyio
async def test_reaction_on_ticket_created_card_does_not_create_duplicate(setup_test_db):
    conn = setup_test_db

    # 1. Insert a Ticket Created card message in DB
    card_id = "card-msg-999"
    ticket_card = {
        "messageId": card_id,
        "chatId": "chat-1",
        "sender": {"userId": None, "displayName": None},
        "message": {
            "text": "🎟️ Ticket Created: SCRUM-118\nTopic Details: Footer defect\nScrum Link: https://jira/SCRUM-118",
        },
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive"}],
        "reactions": [{"userId": "pm-1", "displayName": "Dhruv dobariya", "reactionType": "🎟️"}],
        "jira_issue_key": "SCRUM-118",
        "confirmation_status": "APPROVED",
    }
    store_message(ticket_card)

    with patch("src.services.jira_service.create_jira_issue") as mock_create_jira, \
         patch("src.services.ai_service.extract_jira_ticket") as mock_ai_extract:

        result = await check_and_auto_create_jira_ticket(ticket_card, "PM")

        # Verify it acknowledges existing ticket and never triggers creation or AI extraction
        assert result is not None
        assert result.get("already_existed") is True
        assert result.get("key") == "SCRUM-118"
        mock_create_jira.assert_not_called()
        mock_ai_extract.assert_not_called()


@pytest.mark.anyio
async def test_reacting_to_reply_in_completed_thread_does_not_create_duplicate(setup_test_db):
    conn = setup_test_db

    # 1. Insert parent client issue that was already approved & created
    parent_id = "parent-issue-001"
    parent_msg = {
        "messageId": parent_id,
        "chatId": "chat-1",
        "sender": {"userId": "client-1", "displayName": "Nishi Sharma"},
        "message": {"text": "issue found in the footer of the dashboard ."},
        "jira_issue_key": "SCRUM-118",
        "confirmation_status": "APPROVED",
    }
    store_message(parent_msg)

    # 2. Insert a thread reply (e.g., bot notification card or user acknowledgment in thread)
    reply_id = "reply-msg-002"
    reply_msg = {
        "messageId": reply_id,
        "chatId": "chat-1",
        "replyToId": parent_id,
        "sender": {"userId": "pm-1", "displayName": "Dhruv dobariya"},
        "message": {"text": "Thanks team, this is tracked!"},
        "reactions": [{"userId": "pm-1", "displayName": "Dhruv dobariya", "reactionType": "🎟️"}],
    }
    store_message(reply_msg)

    with patch("src.services.jira_service.create_jira_issue") as mock_create_jira, \
         patch("src.services.ai_service.extract_jira_ticket") as mock_ai_extract:

        result = await check_and_auto_create_jira_ticket(reply_msg, "PM")

        # Must recognize the parent thread already has a ticket and abort without duplicating
        assert result is not None
        assert result.get("already_existed") is True
        assert result.get("key") == "SCRUM-118"
        mock_create_jira.assert_not_called()
        mock_ai_extract.assert_not_called()


@pytest.mark.anyio
async def test_unmapped_card_reaction_returns_none_without_falling_through(setup_test_db):
    conn = setup_test_db

    # An adaptive card with no pending approval in this chat
    card_id = "random-card-123"
    card_msg = {
        "messageId": card_id,
        "chatId": "chat-2",
        "sender": {"userId": None, "displayName": None},
        "message": {"text": ""},
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive"}],
        "reactions": [{"userId": "pm-1", "displayName": "Dhruv dobariya", "reactionType": "👍"}],
    }
    store_message(card_msg)

    with patch("src.services.jira_service.create_jira_issue") as mock_create_jira, \
         patch("src.services.ai_service.extract_jira_ticket") as mock_ai_extract:

        result = await check_and_auto_create_jira_ticket(card_msg, "PM")

        # Must return None and NOT fall through to Step 1 triage
        assert result is None
        mock_create_jira.assert_not_called()
        mock_ai_extract.assert_not_called()


@pytest.mark.anyio
async def test_normal_client_issue_still_triages_correctly(setup_test_db):
    conn = setup_test_db

    client_id = "client-bug-555"
    client_msg = {
        "messageId": client_id,
        "chatId": "chat-1",
        "sender": {"userId": "client-1", "displayName": "Nishi Sharma"},
        "message": {"text": "Critical bug: Checkout payment button fails on submit"},
        "reactions": [{"userId": "pm-1", "displayName": "Dhruv dobariya", "reactionType": "🎟️"}],
    }
    store_message(client_msg)

    mock_ticket = {
        "is_ticket_request": True,
        "summary": "Checkout payment button fails on submit",
        "description": "Critical bug reported during checkout payment",
        "issue_type": "Bug",
        "priority": "High",
        "affected_module": "Payments",
        "suggested_assignee": "Unassigned",
        "issues": [{
            "summary": "Checkout payment button fails on submit",
            "description": "Critical bug reported during checkout payment",
            "issue_type": "Bug",
            "priority": "High",
            "affected_module": "Payments",
            "suggested_assignee": "Unassigned",
        }],
    }

    with patch("src.services.ai_service.extract_jira_ticket", new_callable=AsyncMock) as mock_ai_extract, \
         patch("src.services.teams_notifier.send_pending_approval_notification", new_callable=AsyncMock) as mock_notify:

        mock_ai_extract.return_value = mock_ticket
        mock_notify.return_value = {"success": True}

        result = await check_and_auto_create_jira_ticket(client_msg, "PM")

        # Should successfully triage and move to AWAITING_FINAL_CONFIRMATION
        assert result is not None
        assert result.get("status") == "AWAITING_FINAL_CONFIRMATION"
        mock_ai_extract.assert_called_once()
        mock_notify.assert_called_once()
