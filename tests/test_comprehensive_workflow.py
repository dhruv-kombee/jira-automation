"""
Comprehensive End-to-End Workflow Test Suite
=============================================

Covers the complete lifecycle from client message → PM reaction → issue detection → approval/rejection → Jira ticket creation.

Test Categories:
  1. Reaction Emoji Recognition (exhaustive edge cases)
  2. Role Authorization & PM Access Control
  3. Message Classification: Simple Issues (best → worst detection)
  4. Message Classification: Multi-line / Multi-issue (best → worst)
  5. Message Classification: Non-issues that should NOT create tickets
  6. Attachment Handling: Screenshots, logs, PDFs, mixed, corrupt
  7. Full 2-Step Workflow: Approval path (all emoji variants)
  8. Full 2-Step Workflow: Rejection path (all emoji variants)
  9. Full 2-Step Workflow: No reaction (message should be ignored)
  10. Edge Cases: Duplicate reactions, re-approval after decline, empty messages
  11. Dashboard API Endpoints: confirm-approval, decline-approval
"""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock
from fastapi.testclient import TestClient

from src.services.sender_service import (
    identify_sender_role,
    Roles,
    is_ticket_approval_reaction,
    is_ticket_disapproval_reaction,
    is_pm_approval,
    is_pm_disapproval,
    is_pm_confirmation_approval,
)
from src.services.ai_service import (
    extract_jira_ticket,
    _rule_based_fallback,
    ClassificationState,
)
from src.database import get_db, init_database, close_database
from src.repositories.message_repository import store_message
from src.services.message_service import (
    check_and_auto_create_jira_ticket,
    execute_jira_ticket_creation,
    execute_jira_ticket_decline,
)
from src.app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _init_test_db(tmp_path, name="test.db"):
    close_database()
    db_path = tmp_path / name
    return init_database(str(db_path))


def _store_msg(msg_id, text, reactions=None, sender_name="Dhruv dobariya",
               sender_uid="client-1", chat_id="chat-test", attachments=None):
    """Convenience helper to store a normalized message and return it."""
    normalized = {
        "messageId": msg_id,
        "chatId": chat_id,
        "sender": {"userId": sender_uid, "displayName": sender_name},
        "message": {"text": text, "createdAt": "2026-10-05T12:00:00Z"},
        "reactions": reactions or [],
        "attachments": attachments or [],
    }
    store_message(normalized)
    return normalized


def _make_reaction(user_id, display_name, reaction_type):
    return {"userId": user_id, "displayName": display_name, "reactionType": reaction_type}


PM_REACTION = lambda rtype: _make_reaction("pm-1", "Hemil Ghori", rtype)
CLIENT_REACTION = lambda rtype: _make_reaction("client-1", "Dhruv dobariya", rtype)
DEV_REACTION = lambda rtype: _make_reaction("dev-1", "Musaib Khan", rtype)
UNKNOWN_REACTION = lambda rtype: _make_reaction("unknown-99", "Random User", rtype)


# Mock AI ticket for a single bug
MOCK_SINGLE_BUG = {
    "is_ticket_request": True,
    "classification_state": ClassificationState.CONFIRMED_ISSUE.value,
    "confidence": 0.95,
    "summary": "[Dashboard] Refresh button unresponsive",
    "issue_type": "Bug",
    "priority": "High",
    "affected_module": "Frontend/UI",
    "observed_behavior": "Clicking refresh does nothing",
    "expected_behavior": "Dashboard data should reload",
    "suggested_assignee": "Musaib Khan",
    "description": "Refresh button on main dashboard is non-functional",
    "evidence": ["No network request fired on click"],
    "issues": [{
        "summary": "[Dashboard] Refresh button unresponsive",
        "issue_type": "Bug",
        "priority": "High",
        "affected_module": "Frontend/UI",
        "suggested_assignee": "Musaib Khan",
        "observed_behavior": "Clicking refresh does nothing",
    }],
}

# Mock AI ticket for multiple issues
MOCK_MULTI_ISSUE = {
    "is_ticket_request": True,
    "classification_state": ClassificationState.CONFIRMED_ISSUE.value,
    "confidence": 0.92,
    "summary": "[Login] 500 error on submit",
    "issue_type": "Bug",
    "priority": "High",
    "affected_module": "Authentication",
    "description": "Multiple issues reported",
    "issues": [
        {
            "summary": "[Login] 500 error on submit",
            "issue_type": "Bug",
            "priority": "Highest",
            "affected_module": "Authentication",
            "suggested_assignee": "Hemil Ghori",
        },
        {
            "summary": "[Dashboard] Charts not loading",
            "issue_type": "Bug",
            "priority": "Medium",
            "affected_module": "Frontend/UI",
            "suggested_assignee": "Musaib Khan",
        },
        {
            "summary": "[Settings] Profile page shows stale data",
            "issue_type": "Bug",
            "priority": "Low",
            "affected_module": "Frontend/UI",
            "suggested_assignee": "Musaib Khan",
        },
    ],
}

# Mock AI ticket: non-issue
MOCK_NON_ISSUE = {
    "is_ticket_request": False,
    "classification_state": ClassificationState.GENERAL_MESSAGE.value,
    "confidence": 0.97,
    "summary": "General conversation",
    "description": "Not a ticket request",
}


# ===========================================================================
# CATEGORY 1: Reaction Emoji Recognition (exhaustive)
# ===========================================================================
class TestReactionRecognition:
    """Test that the system correctly identifies approval and disapproval reactions."""

    # --- Approval ---
    @pytest.mark.parametrize("emoji", [
        "🎟️", "🎟", "🎫",                               # Unicode emojis
        "admission ticket", "admission tickets",            # Text names
        "Admission Tickets", "ADMISSION TICKETS",           # Case variants
        ":admission_tickets:", ":admission_ticket:",        # Shortcodes
        ":ticket:", "ticket", "tickets",                    # Shortcodes
    ])
    def test_approval_reactions_recognized(self, emoji):
        assert is_ticket_approval_reaction(emoji) is True

    @pytest.mark.parametrize("emoji", [
        "👍", "❤️", "😂", "😮", "😢", "👏",             # Default Teams quick reactions
        "like", "heart", "laugh", "surprised",             # Teams internal names
        "angry", "sad", "celebrate",                       # Other Teams reactions
        "", None,                                          # Empty/None
        "random_text", "approve", "yes", "ok",             # Non-ticket text
    ])
    def test_non_approval_reactions_rejected(self, emoji):
        assert is_ticket_approval_reaction(emoji) is False

    # --- Disapproval ---
    @pytest.mark.parametrize("emoji", [
        "❌", "✖️", "✖", "🚫", "👎",                     # Unicode emojis
        "cross", "cross mark", "cancel", "decline",         # Text names
        "disapprove", "thumbsdown", "thumbs down",          # More text
        "no", "x",                                          # Short forms
    ])
    def test_disapproval_reactions_recognized(self, emoji):
        assert is_ticket_disapproval_reaction(emoji) is True

    @pytest.mark.parametrize("emoji", [
        "👍", "🎟️", "🎫", "like", "heart",               # Non-disapproval
        "", None, "random",                                 # Edge cases
    ])
    def test_non_disapproval_reactions_rejected(self, emoji):
        assert is_ticket_disapproval_reaction(emoji) is False


# ===========================================================================
# CATEGORY 2: Role Authorization & PM Access Control
# ===========================================================================
class TestRoleAuthorization:
    """Test PM-only approval logic and self-approval mode."""

    def test_pm_approval_with_ticket_emoji(self):
        """PM reacts with 🎟️ → Step 1 approval should pass."""
        reactions = [PM_REACTION("🎟️")]
        assert is_pm_approval(reactions) is True

    def test_pm_approval_with_ticket_variant(self):
        """PM reacts with 🎫 → Step 1 approval should pass."""
        reactions = [PM_REACTION("🎫")]
        assert is_pm_approval(reactions) is True

    def test_pm_thumbs_up_does_not_trigger_step1(self):
        """PM reacts with 👍 → Step 1 should NOT trigger (only ticket emojis for step 1)."""
        reactions = [PM_REACTION("like")]
        assert is_pm_approval(reactions) is False

    def test_pm_thumbs_up_triggers_step2_confirmation(self):
        """PM reacts with 👍 (like) → Step 2 confirmation should pass."""
        reactions = [PM_REACTION("like")]
        assert is_pm_confirmation_approval(reactions) is True

    def test_pm_ticket_emoji_triggers_step2_confirmation(self):
        """PM reacts with 🎟️ → Step 2 confirmation should also pass."""
        reactions = [PM_REACTION("🎟️")]
        assert is_pm_confirmation_approval(reactions) is True

    def test_developer_cannot_approve_when_self_approval_disabled(self):
        """Developer reacts with 🎟️, self-approval OFF → should NOT approve."""
        reactions = [DEV_REACTION("🎟️")]
        assert is_pm_approval(reactions, allow_client=False) is False

    def test_unknown_user_cannot_approve(self):
        """Random unknown user reacts with 🎟️ → should NOT approve."""
        reactions = [UNKNOWN_REACTION("🎟️")]
        assert is_pm_approval(reactions, allow_client=False) is False

    def test_client_can_self_approve_when_enabled(self):
        """Client (Dhruv) reacts with 🎟️ when ALLOW_SELF_APPROVAL=true → should approve."""
        reactions = [CLIENT_REACTION("🎟️")]
        assert is_pm_approval(reactions, allow_client=True) is True

    def test_client_cannot_self_approve_when_disabled(self):
        """Client (Dhruv) reacts with 🎟️ when ALLOW_SELF_APPROVAL=false → should NOT approve."""
        reactions = [CLIENT_REACTION("🎟️")]
        assert is_pm_approval(reactions, allow_client=False) is False

    def test_pm_disapproval_with_cross(self):
        """PM reacts with ❌ → disapproval detected."""
        reactions = [PM_REACTION("❌")]
        assert is_pm_disapproval(reactions) is True

    def test_pm_disapproval_with_thumbs_down(self):
        """PM reacts with 👎 → disapproval detected."""
        reactions = [PM_REACTION("👎")]
        assert is_pm_disapproval(reactions) is True

    def test_non_pm_disapproval_rejected_when_self_off(self):
        """Unknown user reacts with ❌, self-approval OFF → disapproval NOT recognized."""
        reactions = [UNKNOWN_REACTION("❌")]
        assert is_pm_disapproval(reactions, allow_client=False) is False

    def test_empty_reactions_returns_false(self):
        """No reactions at all → all checks return False."""
        assert is_pm_approval([]) is False
        assert is_pm_approval(None) is False
        assert is_pm_disapproval([]) is False
        assert is_pm_disapproval(None) is False
        assert is_pm_confirmation_approval([]) is False
        assert is_pm_confirmation_approval(None) is False

    def test_multiple_reactions_pm_wins(self):
        """Multiple reactions from different users: PM approval should be detected."""
        reactions = [
            DEV_REACTION("❤️"),       # Developer hearts (ignored)
            UNKNOWN_REACTION("👍"),   # Unknown user likes (ignored)
            PM_REACTION("🎟️"),       # PM approves with ticket emoji
        ]
        assert is_pm_approval(reactions) is True

    def test_mixed_approval_and_disapproval_both_detected(self):
        """PM both approves and disapproves (shouldn't happen but test both detections)."""
        reactions = [PM_REACTION("🎟️"), PM_REACTION("❌")]
        assert is_pm_approval(reactions) is True
        assert is_pm_disapproval(reactions) is True


# ===========================================================================
# CATEGORY 3: Role Identification
# ===========================================================================
class TestRoleIdentification:
    """Test sender role identification by user ID and display name."""

    def test_identify_client_by_name(self):
        assert identify_sender_role(display_name="Dhruv dobariya") == Roles.CLIENT

    def test_identify_pm_by_name(self):
        assert identify_sender_role(display_name="Hemil Ghori") == Roles.PM

    def test_identify_developer_by_name(self):
        assert identify_sender_role(display_name="Musaib Khan") == Roles.DEVELOPER

    def test_identify_developer_variant_name(self):
        assert identify_sender_role(display_name="musain") == Roles.DEVELOPER

    def test_identify_unknown(self):
        assert identify_sender_role(display_name="Random User") == Roles.UNKNOWN

    def test_identify_none(self):
        assert identify_sender_role() == Roles.UNKNOWN


# ===========================================================================
# CATEGORY 4: Issue Detection — Simple Messages (Best → Worst)
# ===========================================================================
class TestIssueDetectionSimple:
    """Test AI/heuristic issue detection on single-line messages, from crystal-clear to ambiguous."""

    # --- BEST: Extremely clear issue reports ---
    def test_best_explicit_hash_issue_with_error_code(self):
        """#issue + HTTP error code → CONFIRMED_ISSUE, Bug, High priority."""
        res = _rule_based_fallback("#issue login page returns 500 error", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["classification_state"] == ClassificationState.CONFIRMED_ISSUE.value
        assert res["issue_type"] == "Bug"
        assert "500" in str(res["evidence"])

    def test_best_explicit_bug_tag(self):
        """#bug tag with crash keyword → Confirmed, High priority."""
        res = _rule_based_fallback("#bug app crash on launch", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Bug"
        assert res["priority"] == "High"

    def test_best_explicit_task_tag(self):
        """#task tag → Confirmed issue, Task type."""
        res = _rule_based_fallback("#task configure SSL certificate for production", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Task"

    # --- GOOD: Clear problem description without tags ---
    def test_good_error_keyword(self):
        """Message with 'error' keyword → detected as issue."""
        res = _rule_based_fallback("There is an error in the checkout page", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Bug"

    def test_good_not_working(self):
        """'not working' → detected as issue."""
        res = _rule_based_fallback("search bar is not working properly", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True

    def test_good_broken(self):
        """'broken' → detected as issue."""
        res = _rule_based_fallback("The navigation menu is broken on mobile", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Bug"

    def test_good_fail_keyword(self):
        """'fail' → detected as issue."""
        res = _rule_based_fallback("Payment processing fails for credit card", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True

    # --- MEDIUM: Implicit issues ---
    def test_medium_crash(self):
        """'crash' keyword → High priority Bug."""
        res = _rule_based_fallback("the app keeps crashing when I open settings", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["priority"] == "High"

    def test_medium_404_error(self):
        """404 error code → detected."""
        res = _rule_based_fallback("getting 404 on the profile page", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert "404" in str(res["evidence"])

    # --- POOR: Vague / borderline messages ---
    def test_poor_vague_issue(self):
        """Vague 'issue' mention → still detected by keyword."""
        res = _rule_based_fallback("there seems to be some issue with the page", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True

    def test_poor_fix_keyword(self):
        """'fix' keyword → detected but may be task."""
        res = _rule_based_fallback("can you fix the alignment on the header", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True

    # --- WORST: Should NOT be detected as issues ---
    def test_worst_greeting(self):
        """'Good morning' → NOT an issue."""
        res = _rule_based_fallback("Good morning team!", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False
        assert res["classification_state"] == ClassificationState.GENERAL_MESSAGE.value

    def test_worst_thank_you(self):
        """'Thank you' → NOT an issue."""
        res = _rule_based_fallback("Thank you for the quick response!", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    def test_worst_casual_chat(self):
        """Casual conversation → NOT an issue."""
        res = _rule_based_fallback("Can we hop on a call later today?", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    def test_worst_meeting_message(self):
        """Meeting-related message → NOT an issue."""
        res = _rule_based_fallback("Lets schedule the sprint review for Friday 3pm", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    def test_worst_single_word(self):
        """Single neutral word → NOT an issue."""
        res = _rule_based_fallback("okay", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    def test_worst_emoji_only(self):
        """Just an emoji → NOT an issue."""
        res = _rule_based_fallback("👍", sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False


# ===========================================================================
# CATEGORY 5: Issue Detection — Multi-line / Complex Messages
# ===========================================================================
class TestIssueDetectionComplex:
    """Test complex multi-line messages and multi-issue extraction."""

    def test_multiline_bug_report(self):
        """Multi-line bug report with context."""
        text = """I found a bug on the dashboard page.
When I click the export button, nothing happens.
The console shows a JavaScript error.
This started happening after the last deployment."""
        res = _rule_based_fallback(text, sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Bug"

    def test_multiline_with_steps(self):
        """Multi-line with implicit steps to reproduce."""
        text = """#issue Payment gateway integration broken
1. Go to checkout
2. Enter card details
3. Click pay
4. Error 500 appears"""
        res = _rule_based_fallback(text, sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert res["priority"] == "High"  # 500 → High
        assert "500" in str(res["evidence"])

    def test_multiline_general_conversation(self):
        """Multi-line general conversation → NOT an issue."""
        text = """Hey everyone,
Just wanted to share that the client meeting went well.
They are happy with the progress so far.
Let's keep up the good work!"""
        res = _rule_based_fallback(text, sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    def test_mixed_text_with_code_block(self):
        """Message with inline error code."""
        text = """#issue found this error in logs:
TypeError: Cannot read properties of undefined (reading 'map')
at Dashboard.render (dashboard.js:42)"""
        res = _rule_based_fallback(text, sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is True
        assert "error" in str(res["evidence"]).lower()

    def test_module_detection_frontend(self):
        """Frontend keywords → Frontend/UI module."""
        res = _rule_based_fallback("#issue button on the page is not clickable", sender_name="Dhruv", sender_role="CLIENT")
        assert res["affected_module"] == "Frontend/UI"
        assert res["suggested_assignee"] == "Musaib Khan"

    def test_module_detection_backend(self):
        """Backend keywords → Backend/API module."""
        res = _rule_based_fallback("#issue API endpoint returns 500 server error", sender_name="Dhruv", sender_role="CLIENT")
        assert res["affected_module"] == "Backend/API"
        assert res["suggested_assignee"] == "Hemil Ghori"

    def test_assignee_direct_mention_musaib(self):
        """Direct mention of Musaib → assigned to Musaib Khan."""
        res = _rule_based_fallback("#issue musaib please check the CSS alignment", sender_name="Dhruv", sender_role="CLIENT")
        assert res["suggested_assignee"] == "Musaib Khan"
        assert "mention" in (res.get("assignee_rationale") or "").lower()

    def test_assignee_direct_mention_hemil(self):
        """Direct mention of Hemil → assigned to Hemil Ghori."""
        res = _rule_based_fallback("#issue hemil the database query is slow", sender_name="Dhruv", sender_role="CLIENT")
        assert res["suggested_assignee"] == "Hemil Ghori"

    def test_priority_critical_whole_page(self):
        """'whole page' → High priority."""
        res = _rule_based_fallback("#issue the whole page goes blank after login", sender_name="Dhruv", sender_role="CLIENT")
        assert res["priority"] == "High"

    def test_priority_critical_blocking(self):
        """'blocking' → High priority."""
        res = _rule_based_fallback("#issue blocking bug: users cannot register", sender_name="Dhruv", sender_role="CLIENT")
        assert res["priority"] == "High"

    def test_priority_normal(self):
        """No urgency keywords → Medium priority."""
        res = _rule_based_fallback("#issue the footer link color is wrong", sender_name="Dhruv", sender_role="CLIENT")
        assert res["priority"] == "Medium"


# ===========================================================================
# CATEGORY 6: Attachment Handling
# ===========================================================================
class TestAttachmentHandling:
    """Test issue detection with various attachment types."""

    @pytest.mark.anyio
    async def test_screenshot_attachment_triggers_issue(self):
        """Message with screenshot attachment → should be detected as issue."""
        attachments = [{
            "name": "error_screenshot.png",
            "content_type": "image/png",
            "bytes": b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,  # minimal PNG header
        }]
        res = await extract_jira_ticket(
            "check this", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        assert res["is_ticket_request"] is True
        assert len(res.get("evidence", [])) > 0

    @pytest.mark.anyio
    async def test_log_file_attachment(self):
        """Log file with error stack trace → CONFIRMED_ISSUE."""
        attachments = [{
            "name": "server.log",
            "content_type": "text/plain",
            "bytes": b"2026-10-05 ERROR java.lang.NullPointerException at com.app.Service.run(Service.java:42)\n"
                     b"Caused by: connection refused to database:5432\n"
                     b"FATAL: application crash",
        }]
        res = await extract_jira_ticket(
            "server is down", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        assert res["is_ticket_request"] is True

    @pytest.mark.anyio
    async def test_multiple_attachments(self):
        """Multiple attachments (screenshot + log) → issue detected."""
        attachments = [
            {
                "name": "ui_bug.png",
                "content_type": "image/png",
                "bytes": b"\x89PNG" + b"\x00" * 50,
            },
            {
                "name": "console.log",
                "content_type": "text/plain",
                "bytes": b"Uncaught TypeError: Cannot read property 'length' of null\n"
                         b"at render (app.js:87)",
            },
        ]
        res = await extract_jira_ticket(
            "found issue", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        assert res["is_ticket_request"] is True

    @pytest.mark.anyio
    async def test_attachment_only_no_text(self):
        """Attachment with empty text → should still detect from attachment."""
        attachments = [{
            "name": "crash_dump.log",
            "content_type": "text/plain",
            "bytes": b"FATAL ERROR: Out of memory exception\nProcess terminated",
        }]
        res = await extract_jira_ticket(
            "", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        # Even with empty text, attachment presence triggers detection
        assert res["is_ticket_request"] is True

    @pytest.mark.anyio
    async def test_no_text_no_attachment(self):
        """Empty message with no attachments → NOT an issue."""
        res = await extract_jira_ticket("", attachments=[], sender_name="Dhruv", sender_role="CLIENT")
        assert res["is_ticket_request"] is False

    @pytest.mark.anyio
    async def test_pdf_attachment(self):
        """PDF attachment → should be treated as potential issue evidence."""
        attachments = [{
            "name": "bug_report.pdf",
            "content_type": "application/pdf",
            "bytes": b"%PDF-1.4" + b"\x00" * 100,
        }]
        res = await extract_jira_ticket(
            "Please check attached bug report", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        # With 'bug' keyword + attachment → issue
        assert res["is_ticket_request"] is True

    @pytest.mark.anyio
    async def test_non_issue_with_attachment(self):
        """Greeting message with a benign attachment → heuristic detects due to attachment."""
        attachments = [{
            "name": "photo.jpg",
            "content_type": "image/jpeg",
            "bytes": b"\xff\xd8\xff" + b"\x00" * 50,  # JPEG header
        }]
        res = await extract_jira_ticket(
            "Here is my photo", attachments=attachments,
            sender_name="Dhruv", sender_role="CLIENT",
        )
        # Note: heuristic fallback treats any attachment as potential issue evidence
        # This is a known limitation — the heuristic cannot distinguish benign images
        # In production, Gemini vision would correctly classify this as non-issue
        assert res["is_ticket_request"] is True  # heuristic false-positive (known limitation)


# ===========================================================================
# CATEGORY 7: Full 2-Step Workflow — Approval Path
# ===========================================================================
class TestFullWorkflowApproval:
    """End-to-end test: client message → PM triage reaction → AI detection → PM confirms → Jira created."""

    @pytest.mark.asyncio
    async def test_step1_pm_ticket_emoji_sends_triage_card(self, tmp_path):
        """Step 1: PM reacts with 🎟️ → triage card sent, NO Jira ticket created yet."""
        _init_test_db(tmp_path, "approval_step1.db")
        msg = _store_msg("msg-approval-1", "Navbar search bar returns 404 when pressing enter",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})) as mock_pending, \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create:

            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            mock_create.assert_not_called()  # No Jira ticket in Step 1!
            mock_pending.assert_called_once()  # Triage card sent!
            assert result is not None
            assert result["status"] == "AWAITING_FINAL_CONFIRMATION"

            row = get_db().execute("SELECT confirmation_status FROM messages WHERE message_id = ?", ("msg-approval-1",)).fetchone()
            assert row["confirmation_status"] == "AWAITING_FINAL_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_step2_pm_thumbs_up_creates_ticket(self, tmp_path):
        """Step 2: PM reacts with 👍 on AWAITING message → Jira ticket created."""
        _init_test_db(tmp_path, "approval_step2_thumbs.db")
        msg = _store_msg("msg-thumbs-1", "Dashboard export button broken",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1: Triage
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: PM approves with 👍
        msg["reactions"] = [PM_REACTION("like")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-100", "url": "https://test.atlassian.net/browse/SCRUM-100", "summary": "Test"})) as mock_create, \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            mock_create.assert_called_once()
            assert result["key"] == "SCRUM-100"

            row = get_db().execute("SELECT confirmation_status, jira_issue_key FROM messages WHERE message_id = ?", ("msg-thumbs-1",)).fetchone()
            assert row["confirmation_status"] == "APPROVED"
            assert row["jira_issue_key"] == "SCRUM-100"

    @pytest.mark.asyncio
    async def test_step2_pm_ticket_emoji_creates_ticket(self, tmp_path):
        """Step 2: PM reacts with 🎟️ again → Jira ticket created."""
        _init_test_db(tmp_path, "approval_step2_ticket.db")
        msg = _store_msg("msg-ticket-2", "API endpoint crashing",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: PM approves with 🎟️
        msg["reactions"] = [PM_REACTION("🎟️")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-101", "url": "https://test.atlassian.net/browse/SCRUM-101", "summary": "Test"})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result["key"] == "SCRUM-101"

    @pytest.mark.asyncio
    async def test_step2_pm_ticket_alt_emoji_creates_ticket(self, tmp_path):
        """Step 2: PM reacts with 🎫 → Jira ticket created."""
        _init_test_db(tmp_path, "approval_step2_alt.db")
        msg = _store_msg("msg-alt-3", "Form validation broken",
                         reactions=[PM_REACTION("🎫")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        msg["reactions"] = [PM_REACTION("🎫")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-102", "url": "https://test.atlassian.net/browse/SCRUM-102", "summary": "Test"})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result["key"] == "SCRUM-102"


# ===========================================================================
# CATEGORY 8: Full 2-Step Workflow — Rejection Path
# ===========================================================================
class TestFullWorkflowRejection:
    """End-to-end test: client message → PM triage → PM rejects → NO Jira ticket."""

    @pytest.mark.asyncio
    async def test_reject_with_cross_emoji(self, tmp_path):
        """PM rejects with ❌ → DECLINED, zero Jira tickets."""
        _init_test_db(tmp_path, "reject_cross.db")
        msg = _store_msg("msg-reject-1", "Search bar broken",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: PM rejects with ❌
        msg["reactions"] = [PM_REACTION("❌")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create, \
             patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})) as mock_declined:
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            mock_create.assert_not_called()
            mock_declined.assert_called_once()
            assert result["status"] == "DECLINED"

            row = get_db().execute("SELECT confirmation_status, jira_issue_key FROM messages WHERE message_id = ?", ("msg-reject-1",)).fetchone()
            assert row["confirmation_status"] == "DECLINED"
            assert row["jira_issue_key"] is None

    @pytest.mark.asyncio
    async def test_reject_with_thumbs_down(self, tmp_path):
        """PM rejects with 👎 → DECLINED."""
        _init_test_db(tmp_path, "reject_thumbsdown.db")
        msg = _store_msg("msg-reject-2", "Minor UI glitch",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        msg["reactions"] = [PM_REACTION("👎")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create, \
             patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            mock_create.assert_not_called()
            assert result["status"] == "DECLINED"


# ===========================================================================
# CATEGORY 9: No Reaction / Ignored Messages
# ===========================================================================
class TestNoReaction:
    """Test that messages without PM approval reactions are ignored."""

    @pytest.mark.asyncio
    async def test_no_reaction_at_all(self, tmp_path):
        """Message with zero reactions → returns None."""
        _init_test_db(tmp_path, "no_reaction.db")
        msg = _store_msg("msg-no-rx-1", "Dashboard page broken", reactions=[])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is None

    @pytest.mark.asyncio
    async def test_non_ticket_reaction_only(self, tmp_path):
        """Only a ❤️ reaction (non-ticket) → returns None."""
        _init_test_db(tmp_path, "heart_reaction.db")
        msg = _store_msg("msg-heart-1", "Bug in payment", reactions=[PM_REACTION("❤️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is None

    @pytest.mark.asyncio
    async def test_developer_reaction_ignored(self, tmp_path):
        """Developer reacts with 🎟️ but self-approval is off → returns None."""
        _init_test_db(tmp_path, "dev_reaction.db")
        msg = _store_msg("msg-dev-1", "Server error 500",
                         reactions=[DEV_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch.object(pytest.importorskip("src.config").config.roles, "allow_self_approval", False):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is None

    @pytest.mark.asyncio
    async def test_non_issue_with_pm_approval_ignored(self, tmp_path):
        """PM approves a non-issue message → AI says not a ticket → returns None."""
        _init_test_db(tmp_path, "non_issue_approved.db")
        msg = _store_msg("msg-non-issue-1", "Thanks for the update everyone!",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_NON_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock()) as mock_pending, \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create:
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            assert result is None
            mock_pending.assert_not_called()
            mock_create.assert_not_called()


# ===========================================================================
# CATEGORY 10: Edge Cases
# ===========================================================================
class TestEdgeCases:
    """Edge cases: duplicate approvals, re-approval after decline, already created tickets."""

    @pytest.mark.asyncio
    async def test_already_created_ticket_returns_existing(self, tmp_path):
        """If ticket already exists in Jira → returns existing key, no duplicate creation."""
        _init_test_db(tmp_path, "already_created.db")
        msg = _store_msg("msg-existing-1", "Bug in nav")

        # Manually set the ticket as already created
        get_db().execute(
            "UPDATE messages SET jira_issue_key = 'SCRUM-50', jira_issue_url = 'https://test.atlassian.net/browse/SCRUM-50', confirmation_status = 'APPROVED' WHERE message_id = ?",
            ("msg-existing-1",),
        )

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True):
            result = await execute_jira_ticket_creation("msg-existing-1")
            assert result["success"] is True
            assert result["key"] == "SCRUM-50"
            assert result["already_existed"] is True

    @pytest.mark.asyncio
    async def test_decline_then_ignore_further_reactions(self, tmp_path):
        """After DECLINED, further non-approval reactions should be ignored."""
        _init_test_db(tmp_path, "decline_ignore.db")
        msg = _store_msg("msg-decline-ign-1", "Minor issue",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: Decline
        msg["reactions"] = [PM_REACTION("❌")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 3: After decline, a ❤️ reaction should NOT re-trigger
        msg["reactions"] = [PM_REACTION("❤️")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is None

    @pytest.mark.asyncio
    async def test_decline_then_re_approve_with_ticket_emoji(self, tmp_path):
        """After DECLINED, PM can re-approve with 🎟️ to start Step 1 again."""
        _init_test_db(tmp_path, "decline_reapprove.db")
        msg = _store_msg("msg-reapprove-1", "Login page broken",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: Decline
        msg["reactions"] = [PM_REACTION("❌")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 3: Re-approve with ticket emoji → should start Step 1 again
        msg["reactions"] = [PM_REACTION("🎟️")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_SINGLE_BUG)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is not None
            assert result["status"] == "AWAITING_FINAL_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_message_not_found_in_db(self, tmp_path):
        """execute_jira_ticket_creation with unknown message_id → returns error."""
        _init_test_db(tmp_path, "not_found.db")
        result = await execute_jira_ticket_creation("nonexistent-msg-99999")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_decline_not_found_in_db(self, tmp_path):
        """execute_jira_ticket_decline with unknown message_id → returns error."""
        _init_test_db(tmp_path, "decline_not_found.db")
        result = await execute_jira_ticket_decline("nonexistent-msg-88888")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_jira_not_configured_ignored(self, tmp_path):
        """If Jira is not configured → reactions ignored silently."""
        _init_test_db(tmp_path, "jira_off.db")
        msg = _store_msg("msg-jira-off-1", "Bug found",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=False):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")
            assert result is None


# ===========================================================================
# CATEGORY 11: Dashboard API Endpoints
# ===========================================================================
class TestDashboardEndpoints:
    """Test the FastAPI dashboard approval/decline HTTP endpoints."""

    def test_health_endpoint(self):
        client = TestClient(app)
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_confirm_approval_post_not_found(self, tmp_path):
        """POST /api/jira/confirm-approval with invalid message → 400."""
        _init_test_db(tmp_path, "api_not_found.db")
        client = TestClient(app)
        res = client.post("/api/jira/confirm-approval/nonexistent-msg-77777")
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_decline_approval_post_not_found(self, tmp_path):
        """POST /api/jira/decline-approval with invalid message → 400."""
        _init_test_db(tmp_path, "api_decline_not_found.db")
        client = TestClient(app)
        res = client.post("/api/jira/decline-approval/nonexistent-msg-66666")
        assert res.status_code == 400


# ===========================================================================
# CATEGORY 12: Multi-Issue Workflow
# ===========================================================================
class TestMultiIssueWorkflow:
    """Test detection and creation of multiple issues from a single message."""

    @pytest.mark.asyncio
    async def test_multi_issue_triage_card(self, tmp_path):
        """PM reacts on multi-issue message → triage card shows all issues."""
        _init_test_db(tmp_path, "multi_issue.db")
        msg = _store_msg("msg-multi-1",
                         "1. Login page 500 error\n2. Dashboard charts not loading\n3. Settings page stale data",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})) as mock_pending:
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            assert result["status"] == "AWAITING_FINAL_CONFIRMATION"
            assert result["issues_count"] == 3
            mock_pending.assert_called_once()

            # Verify the issues list was passed correctly
            call_kwargs = mock_pending.call_args
            issues_arg = call_kwargs.kwargs.get("issues") or call_kwargs[1].get("issues")
            assert len(issues_arg) == 3

    @pytest.mark.asyncio
    async def test_multi_issue_approve_all(self, tmp_path):
        """PM approves multi-issue → all 3 Jira tickets created."""
        _init_test_db(tmp_path, "multi_approve.db")
        msg = _store_msg("msg-multi-approve-1",
                         "1. Login 500\n2. Charts broken\n3. Stale settings",
                         reactions=[PM_REACTION("🎟️")])

        # Step 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Step 2: Approve all
        jira_call_count = 0

        async def mock_create_jira(**kwargs):
            nonlocal jira_call_count
            jira_call_count += 1
            return {
                "success": True,
                "key": f"SCRUM-{200 + jira_call_count}",
                "url": f"https://test.atlassian.net/browse/SCRUM-{200 + jira_call_count}",
                "summary": kwargs.get("summary", "Test"),
            }

        msg["reactions"] = [PM_REACTION("like")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", side_effect=mock_create_jira), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            assert result["success"] is True
            assert jira_call_count == 3  # All 3 issues created
            assert "SCRUM-201" in result["key"]

    @pytest.mark.asyncio
    async def test_multi_issue_reject_all(self, tmp_path):
        """PM rejects multi-issue → zero Jira tickets."""
        _init_test_db(tmp_path, "multi_reject.db")
        msg = _store_msg("msg-multi-reject-1",
                         "1. Login error\n2. Charts issue",
                         reactions=[PM_REACTION("🎟️")])

        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        msg["reactions"] = [PM_REACTION("❌")]
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_create, \
             patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})):
            result = await check_and_auto_create_jira_ticket(msg, "CLIENT")

            mock_create.assert_not_called()
            assert result["status"] == "DECLINED"


# ===========================================================================
# 13. Granular Multi-Issue Actions (Confirm/Decline specific sub-issues)
# ===========================================================================
class TestGranularIssueActions:
    @pytest.mark.asyncio
    async def test_confirm_issue_specific_index(self, tmp_path):
        _init_test_db(tmp_path, "granular_confirm.db")
        msg = _store_msg("msg-gran-1", "Multiple issues", reactions=[PM_REACTION("🎟️")])
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # Approve only issue index 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-55", "url": "https://test.atlassian.net/browse/SCRUM-55"})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            res = await execute_jira_ticket_creation("msg-gran-1", approver_name="PM Santosh Yadav", issue_idx=1)
            assert res["success"] is True
            assert res["key"] == "SCRUM-55"

    @pytest.mark.asyncio
    async def test_confirm_issue_preserves_multiple_keys(self, tmp_path):
        _init_test_db(tmp_path, "granular_multikey.db")
        msg = _store_msg("msg-gran-multi", "Multiple issues", reactions=[PM_REACTION("🎟️")])
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        # First approve issue index 0
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-101", "url": "url1"})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            res1 = await execute_jira_ticket_creation("msg-gran-multi", issue_idx=0)
            assert res1["key"] == "SCRUM-101"

        # Next approve issue index 1
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={"success": True, "key": "SCRUM-102", "url": "url2"})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={"success": True})):
            res2 = await execute_jira_ticket_creation("msg-gran-multi", issue_idx=1)
            assert "SCRUM-101" in res2["key"]
            assert "SCRUM-102" in res2["key"]

    @pytest.mark.asyncio
    async def test_decline_issue_specific_index(self, tmp_path):
        _init_test_db(tmp_path, "granular_decline.db")
        msg = _store_msg("msg-gran-dec", "Multiple issues", reactions=[PM_REACTION("🎟️")])
        with patch("src.config.JiraConfig.is_configured", new_callable=PropertyMock, return_value=True), \
             patch("src.services.ai_service.extract_jira_ticket", AsyncMock(return_value=MOCK_MULTI_ISSUE)), \
             patch("src.services.teams_notifier.send_pending_approval_notification", AsyncMock(return_value={"success": True})):
            await check_and_auto_create_jira_ticket(msg, "CLIENT")

        with patch("src.services.teams_notifier.send_ticket_declined_notification", AsyncMock(return_value={"success": True})):
            res = await execute_jira_ticket_decline("msg-gran-dec", issue_idx=0)
            assert res["success"] is True
            assert "Issue #1" in res["declined_label"]

    def test_dashboard_granular_endpoints(self, tmp_path):
        _init_test_db(tmp_path, "dash_granular.db")
        _store_msg("msg-dash-gran", "Multi issue text")
        client = TestClient(app)
        with patch("src.services.message_service.execute_jira_ticket_creation", AsyncMock(return_value={"success": True, "key": "KEY-1", "url": "http://x"})):
            r1 = client.get("/api/jira/confirm-issue/msg-dash-gran/0?auto=1")
            assert r1.status_code == 200
            assert "KEY-1" in r1.text or "Approved" in r1.text

        with patch("src.services.message_service.execute_jira_ticket_decline", AsyncMock(return_value={"success": True, "status": "DECLINED"})):
            r2 = client.get("/api/jira/decline-issue/msg-dash-gran/0")
            assert r2.status_code == 200
            assert "Rejected" in r2.text


# ===========================================================================
# 14. Document & Attachment Text Extraction
# ===========================================================================
class TestDocumentAndAttachmentParsing:
    def test_extract_text_from_log_with_octet_stream(self):
        from src.services.ai_service import extract_text_from_attachment
        log_bytes = b"2026-10-05 ERROR [server] NullPointerException in payment_gateway.py:42"
        text = extract_text_from_attachment(log_bytes, "crash.log", "application/octet-stream")
        assert text is not None
        assert "NullPointerException" in text

    def test_extract_text_from_json_and_csv(self):
        from src.services.ai_service import extract_text_from_attachment
        json_bytes = b'{"status": "failure", "code": 500, "message": "database timed out"}'
        text = extract_text_from_attachment(json_bytes, "error.json", "application/json")
        assert text is not None
        assert "database timed out" in text

    def test_fallback_with_extracted_attachment_text(self):
        from src.services.ai_service import _rule_based_fallback
        res = _rule_based_fallback(
            "",
            sender_name="Dhruv",
            sender_role="CLIENT",
            has_attachments=True,
            attachment_texts=["NullPointerException in auth.py"],
            attachment_names=["server_crash.log"],
        )
        assert res["is_ticket_request"] is True
        assert res["issue_type"] == "Bug"
        assert "server_crash.log" in res["summary"] or "server_crash.log" in str(res["evidence"])


# ===========================================================================
# 15. Thread Grouping & Duplicate Ticket Detection
# ===========================================================================
class TestThreadGroupingAndDuplicateDetection:
    def test_get_parent_message(self, tmp_path):
        from src.repositories.message_repository import get_parent_message
        _init_test_db(tmp_path, "thread.db")
        _store_msg("msg-parent-1", "Original bug report on checkout page")
        _store_msg("msg-child-1", "Here is screenshot", reactions=[], sender_name="Dhruv")
        db = get_db()
        db.execute("UPDATE messages SET reply_to_id = 'msg-parent-1' WHERE message_id = 'msg-child-1'")

        found_parent = get_parent_message("msg-parent-1")
        assert found_parent is not None
        assert "Original bug report" in found_parent["message_text"]

    def test_find_recent_similar_tickets(self, tmp_path):
        from src.repositories.message_repository import find_recent_similar_tickets
        _init_test_db(tmp_path, "duplicates.db")
        _store_msg("msg-dup-1", "Payment gateway checkout failing with 500 error", chat_id="chat-123")
        db = get_db()
        db.execute("UPDATE messages SET jira_issue_key = 'PAY-99', jira_issue_url = 'https://jira/PAY-99' WHERE message_id = 'msg-dup-1'")

        # New message with similar summary
        dups = find_recent_similar_tickets("Payment gateway checkout failing with 500 error", chat_id="chat-123")
        assert len(dups) >= 1
        assert dups[0]["key"] == "PAY-99"

    def test_pending_approval_card_renders_duplicate_warning(self):
        from src.services.teams_notifier import build_pending_approval_card
        card = build_pending_approval_card(
            message_id="msg-card-dup",
            issues=[{"summary": "Checkout button broken"}],
            reporter="Dhruv",
            raw_message="Checkout broken",
            created_at="2026-10-05",
            duplicate_warning={"key": "JIRA-77", "url": "https://jira/JIRA-77", "confidence": 0.85},
            extractor_mode="Gemini 3.5 Flash-Lite",
        )
        card_json = json.dumps(card)
        assert "Potential duplicate" in card_json or "Potential Duplicate" in card_json
        assert "Ticket Confirmation" in card_json
        assert "Gemini 3.5 Flash-Lite" not in card_json
        assert "Approve" in card_json
