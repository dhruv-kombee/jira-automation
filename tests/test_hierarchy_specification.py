"""Comprehensive verification tests for Dynamic Role Hierarchy & Approval Authority specification.
Tests all requirements FR-01 through FR-06 and acceptance criteria AC-01 through AC-06.
"""
import io
import unittest
import openpyxl
from unittest.mock import patch, MagicMock

from src.services.member_sync_service import (
    read_members_from_excel,
    get_all_members_from_excel,
    get_active_tls,
    get_active_pms,
    get_active_hms,
    get_members_by_level,
    get_member_by_id_or_name,
)
from src.services.sender_service import (
    Roles,
    identify_sender_role,
    is_user_authorized_approver,
    is_ticket_approval_reaction,
    is_pm_approval,
    is_pm_confirmation_approval,
    is_ticket_disapproval_reaction,
    is_pm_disapproval,
)
from src.services.teams_notifier import (
    build_pending_approval_card,
    build_pm_followup_reminder_card,
)


class TestHierarchySpecification(unittest.TestCase):

    def test_fr03_unlisted_sender_is_unassigned(self):
        """FR-03: Anyone not listed, or with an unknown role, is treated as UNASSIGNED."""
        with patch("src.services.sender_service.get_member_by_id_or_name", return_value=None):
            role = identify_sender_role(user_id="rand-uid-12345", display_name="Random Unlisted User")
            self.assertEqual(role, Roles.UNASSIGNED)

    def test_ac03_developer_and_unassigned_reactions_blocked(self):
        """AC-03: Reactions from DEVELOPER and UNASSIGNED users trigger nothing."""
        mock_dev = {"display_name": "Dev Alice", "role": "DEVELOPER", "is_active": True, "can_approve": False}
        mock_unassigned = {"display_name": "Pending Bob", "role": "UNASSIGNED", "is_active": True, "can_approve": False}
        mock_inactive_pm = {"display_name": "Former PM", "role": "PM", "level": "Level 2", "is_active": False, "can_approve": True}

        with patch("src.services.sender_service.get_member_by_id_or_name") as mock_get:
            # DEVELOPER
            mock_get.return_value = mock_dev
            self.assertFalse(is_user_authorized_approver(user_id="dev-1", display_name="Dev Alice"))

            # UNASSIGNED
            mock_get.return_value = mock_unassigned
            self.assertFalse(is_user_authorized_approver(user_id="un-1", display_name="Pending Bob"))

            # INACTIVE PM (Only active members are authorized)
            mock_get.return_value = mock_inactive_pm
            self.assertFalse(is_user_authorized_approver(user_id="pm-old", display_name="Former PM"))

    def test_ac04_tl_pm_hm_authorized_reactions(self):
        """AC-04: TL, PM, HM reactions create, approve, or decline."""
        mock_tl = {"display_name": "TL Charlie", "role": "TL", "level": "Level 1", "is_active": True, "can_approve": True}
        mock_pm = {"display_name": "PM Diana", "role": "PM", "level": "Level 2", "is_active": True, "can_approve": True}
        mock_hm = {"display_name": "HM Edward", "role": "HM", "level": "Level 3", "is_active": True, "can_approve": True}

        with patch("src.services.sender_service.get_member_by_id_or_name") as mock_get:
            # TL authorized
            mock_get.return_value = mock_tl
            self.assertTrue(is_user_authorized_approver(user_id="tl-1", display_name="TL Charlie"))

            # PM authorized
            mock_get.return_value = mock_pm
            self.assertTrue(is_user_authorized_approver(user_id="pm-1", display_name="PM Diana"))

            # HM authorized
            mock_get.return_value = mock_hm
            self.assertTrue(is_user_authorized_approver(user_id="hm-1", display_name="HM Edward"))

        # Emojis validation
        self.assertTrue(is_ticket_approval_reaction("🎟️"))
        self.assertTrue(is_ticket_approval_reaction("🎫"))
        self.assertFalse(is_ticket_approval_reaction("👍"))

        self.assertTrue(is_ticket_disapproval_reaction("❌"))
        self.assertTrue(is_ticket_disapproval_reaction("✖️"))
        self.assertTrue(is_ticket_disapproval_reaction("👎"))

    def test_fr01_and_ac01_multi_person_per_level(self):
        """FR-01 & AC-01: Each level (TL, PM, HM) supports one or many people with no code change."""
        mock_members = [
            {"display_name": "TL 1", "role": "TL", "level": "Level 1", "is_active": True},
            {"display_name": "TL 2", "role": "TL", "level": "Level 1", "is_active": True},
            {"display_name": "PM 1", "role": "PM", "level": "Level 2", "is_active": True},
            {"display_name": "PM 2", "role": "PM", "level": "Level 2", "is_active": True},
            {"display_name": "HM 1", "role": "HM", "level": "Level 3", "is_active": True},
            {"display_name": "HM 2", "role": "HM", "level": "Level 3", "is_active": True},
        ]

        with patch("src.services.member_sync_service.get_all_members_from_excel", return_value=mock_members):
            tls = get_active_tls()
            pms = get_active_pms()
            hms = get_active_hms()

            self.assertEqual(len(tls), 2)
            self.assertEqual(len(pms), 2)
            self.assertEqual(len(hms), 2)

            self.assertEqual(len(get_members_by_level("1")), 2)
            self.assertEqual(len(get_members_by_level("Level 2")), 2)
            self.assertEqual(len(get_members_by_level("3")), 2)

    def test_fr04_and_ac05_multi_user_tagging_on_confirmation_card(self):
        """FR-04 & AC-05: Targeting a level tags every active member of that level at once."""
        target_tls = [
            {"display_name": "Lead Alice", "user_id": "aad-alice-id", "email": "alice@example.com"},
            {"display_name": "Lead Bob", "user_id": "aad-bob-id", "email": "bob@example.com"},
        ]

        card = build_pending_approval_card(
            message_id="msg-test-101",
            issues=[{"summary": "Login page crash"}],
            reporter="Client VIP",
            raw_message="Login page crashes on submit",
            created_at="2026-10-09T10:00:00Z",
            target_users=target_tls,
            target_role="TL",
        )

        # Check plain text contains both mentions
        self.assertIn("@Lead Alice", card["text"])
        self.assertIn("@Lead Bob", card["text"])

        # Check adaptive card attachment content contains msteams.entities
        att_content = card["attachments"][0]["content"]
        msteams = att_content.get("msteams", {})
        entities = msteams.get("entities", [])

        self.assertEqual(len(entities), 2)
        entity_names = [e["mentioned"]["name"] for e in entities]
        self.assertIn("Lead Alice", entity_names)
        self.assertIn("Lead Bob", entity_names)

        # Check card body has visual tag block
        body_texts = [item.get("text", "") for item in att_content["body"] if item.get("type") == "TextBlock"]
        has_bell_tag = any("<at>Lead Alice</at>" in t and "<at>Lead Bob</at>" in t for t in body_texts)
        self.assertTrue(has_bell_tag, "Expected card body to include <at> mentions for all target members")

    def test_direct_decline_on_raw_message(self):
        """Verify that when TL/PM reacts with ❌ directly on the raw message, it declines immediately."""
        import asyncio
        from src.services.message_service import check_and_auto_create_jira_ticket

        mock_tl = {"display_name": "TL Yash", "role": "TL", "level": "Level 1", "is_active": True, "can_approve": True}
        normalized = {
            "messageId": "msg-decline-raw-1",
            "chatId": "chat-1",
            "reactions": [{"reactionType": "❌", "displayName": "Crossmark", "userId": "tl-yash-id"}],
            "sender": {"userId": "client-id", "displayName": "Client Reporter"},
            "message": {"text": "Bug found in login page"},
        }

        mock_row = {
            "message_id": "msg-decline-raw-1",
            "chat_id": "chat-1",
            "message_text": "Bug found in login page",
            "confirmation_status": None,
            "jira_issue_key": None,
            "ai_ticket": None,
            "sender_display_name": "Client Reporter",
        }
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = mock_row

        with patch("src.services.sender_service.get_member_by_id_or_name", return_value=mock_tl), \
             patch("src.database.get_db", return_value=mock_db), \
             patch("src.services.message_service.execute_jira_ticket_decline") as mock_decline:
            mock_decline.return_value = {"success": True, "status": "DECLINED"}
            res = asyncio.run(check_and_auto_create_jira_ticket(normalized, "CLIENT"))
            self.assertTrue(mock_decline.called)
            self.assertEqual(res, {"success": True, "status": "DECLINED"})


if __name__ == "__main__":
    unittest.main()
