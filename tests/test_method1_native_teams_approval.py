import sys
sys.path.insert(0, '.')
import json
import unittest
import tempfile
import os
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.app import app
from src.database import init_database, close_database, get_db
from src.services.teams_notifier import build_pending_approval_card

MOCK_ROSTER = [
    {"user_id": "hm-1", "display_name": "Hemil Ghori", "role": "HM", "level": "Level 3", "can_approve": 1, "is_active": 1, "specialty": "Scrum Master"},
    {"user_id": "pm-1", "display_name": "Musaib Khan", "role": "PM", "level": "Level 2", "can_approve": 1, "is_active": 1, "specialty": "Product Manager"},
    {"user_id": "tl-1", "display_name": "Yash Kalkani", "role": "TL", "level": "Level 1", "can_approve": 1, "is_active": 1, "specialty": "Tech Lead"},
    {"user_id": "dev-1", "display_name": "Nishi Sharma", "role": "DEVELOPER", "level": "-", "can_approve": 0, "is_active": 1, "specialty": "AI Developer"},
    {"user_id": "dev-2", "display_name": "Santosh Yadav", "role": "DEVELOPER", "level": "-", "can_approve": 0, "is_active": 1, "specialty": "Backend Engineer"},
    {"user_id": "client-1", "display_name": "Dhruv dobariya", "role": "CLIENT", "level": "-", "can_approve": 0, "is_active": 1, "specialty": "Client Product Owner"},
]

def mock_get_member(user_id=None, display_name=None):
    for m in MOCK_ROSTER:
        if user_id and m["user_id"].lower() == user_id.lower():
            return m
        if display_name and m["display_name"].lower() == display_name.lower():
            return m
    return None

def mock_get_all_members():
    return list(MOCK_ROSTER)


class TestMethod1NativeTeamsApproval(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.temp_dir.name, "test_native_teams.db")
        init_database(self.db_file)
        self.client = TestClient(app)

    def tearDown(self):
        close_database()
        self.temp_dir.cleanup()

    @patch("src.services.member_sync_service.get_all_members_from_excel", side_effect=mock_get_all_members)
    def test_card_structure_zero_openurl_and_choice_set_present(self, mock_roster):
        """Verify build_pending_approval_card generates Input.ChoiceSet dropdown and Action.Submit with ZERO Action.OpenUrl."""
        issues = [{"summary": "Checkout payment gateway failing", "suggested_assignee": "Nishi Sharma"}]
        card = build_pending_approval_card(
            message_id="msg-card-native-1",
            issues=issues,
            reporter="Dhruv dobariya",
            raw_message="Payment broken",
            created_at="2026-10-12T10:00:00Z",
        )
        card_str = json.dumps(card)

        # 1. Zero Action.OpenUrl anywhere in card (No browser links!)
        self.assertNotIn("Action.OpenUrl", card_str)
        self.assertNotIn("/api/jira/confirm-approval", card_str)
        self.assertNotIn("/api/jira/decline-approval", card_str)

        # 2. Input.ChoiceSet exists with id selected_assignee and pre-selected to Nishi Sharma
        body = card["attachments"][0]["content"]["body"]
        choiceset = None
        for el in body:
            if el.get("type") == "Container":
                for item in el.get("items", []):
                    if item.get("type") == "Input.ChoiceSet":
                        choiceset = item
                        break
        self.assertIsNotNone(choiceset)
        self.assertEqual(choiceset["id"], "selected_assignee")
        self.assertEqual(choiceset["value"], "Nishi Sharma")

        # 3. Actions are native Action.Submit for both Approve and Decline
        actions = card["attachments"][0]["content"]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["type"], "Action.Submit")
        self.assertIn("Approve", actions[0]["title"])
        self.assertEqual(actions[0]["data"]["action"], "approve")
        self.assertEqual(actions[0]["data"]["message_id"], "msg-card-native-1")

        self.assertEqual(actions[1]["type"], "Action.Submit")
        self.assertIn("Decline", actions[1]["title"])
        self.assertEqual(actions[1]["data"]["action"], "decline")
        self.assertEqual(actions[1]["data"]["message_id"], "msg-card-native-1")

    @patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member)
    def test_native_card_action_unauthorized_developer_blocked(self, mock_mem, mock_snd):
        """Verify Developer clicking Approve/Decline in Teams is strictly blocked with 403 and access denied card."""
        payload = {
            "action": "approve",
            "message_id": "msg-dev-blocked-1",
            "selected_assignee": "Santosh Yadav",
            "user": {
                "id": "dev-1",
                "name": "Nishi Sharma",
            },
        }

        res = self.client.post("/webhooks/teams/actions", json=payload)
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertIn("Authority Check Failed", data["error"])
        self.assertIn("DEVELOPER", data["error"])
        self.assertIn("Access Denied", json.dumps(data["card"]))

    @patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member)
    def test_native_card_action_unauthorized_client_blocked(self, mock_mem, mock_snd):
        """Verify Client clicking Approve/Decline in Teams is strictly blocked with 403."""
        payload = {
            "action": "approve",
            "message_id": "msg-client-blocked-1",
            "selected_assignee": "Nishi Sharma",
            "user": {
                "id": "client-1",
                "name": "Dhruv dobariya",
            },
        }

        res = self.client.post("/webhooks/teams/actions", json=payload)
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertIn("CLIENT", data["error"])

    @patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.message_service.execute_jira_ticket_creation", new_callable=AsyncMock)
    def test_native_card_action_authorized_pm_creates_ticket(self, mock_exec, mock_mem, mock_snd):
        """Verify PM Musaib Khan clicking Approve creates ticket with selected assignee."""
        mock_exec.return_value = {
            "success": True,
            "key": "SCRUM-901",
            "url": "https://jira/browse/SCRUM-901",
        }

        payload = {
            "action": "approve",
            "message_id": "msg-pm-auth-1",
            "selected_assignee": "Santosh Yadav",
            "from": {
                "id": "pm-1",
                "name": "Musaib Khan",
            },
        }

        res = self.client.post("/webhooks/teams/actions", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["key"], "SCRUM-901")
        mock_exec.assert_called_once_with(
            message_id="msg-pm-auth-1",
            approver_name="PM Musaib Khan",
            issue_idx=None,
            assignee_override="Santosh Yadav",
        )

    @patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.message_service.execute_jira_ticket_creation", new_callable=AsyncMock)
    def test_native_card_action_authorized_tl_creates_ticket(self, mock_exec, mock_mem, mock_snd):
        """Verify TL Yash Kalkani clicking Approve creates ticket."""
        mock_exec.return_value = {
            "success": True,
            "key": "SCRUM-902",
            "url": "https://jira/browse/SCRUM-902",
        }

        payload = {
            "action": "approve",
            "message_id": "msg-tl-auth-1",
            "selected_assignee": "Nishi Sharma",
            "from": {
                "id": "tl-1",
                "name": "Yash Kalkani",
            },
        }

        res = self.client.post("/api/teams/actions", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["key"], "SCRUM-902")
        mock_exec.assert_called_once_with(
            message_id="msg-tl-auth-1",
            approver_name="TL Yash Kalkani",
            issue_idx=None,
            assignee_override="Nishi Sharma",
        )

    @patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member)
    @patch("src.services.message_service.execute_jira_ticket_decline", new_callable=AsyncMock)
    def test_native_card_action_authorized_decline(self, mock_decline, mock_mem, mock_snd):
        """Verify HM Hemil Ghori clicking Decline successfully rejects ticket."""
        mock_decline.return_value = {"success": True}

        payload = {
            "action": "decline",
            "message_id": "msg-hm-decline-1",
            "reason": "Not a bug",
            "from": {
                "id": "hm-1",
                "name": "Hemil Ghori",
            },
        }

        res = self.client.post("/webhooks/teams/actions", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        mock_decline.assert_called_once_with(
            message_id="msg-hm-decline-1",
            approver_name="HM Hemil Ghori",
            reason="Not a bug",
            issue_idx=None,
        )


if __name__ == "__main__":
    unittest.main()
