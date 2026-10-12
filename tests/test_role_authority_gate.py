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
from src.services.sender_service import (
    is_user_authorized_approver,
    is_pm_approval,
    is_pm_confirmation_approval,
    is_pm_disapproval,
    is_role_authorized,
)

MOCK_ROSTER = [
    {"user_id": "hm-1", "display_name": "Hemil Ghori", "role": "HM", "level": "Level 3", "can_approve": 1, "is_active": 1},
    {"user_id": "pm-1", "display_name": "Musaib Khan", "role": "PM", "level": "Level 2", "can_approve": 1, "is_active": 1},
    {"user_id": "tl-1", "display_name": "Nishi Sharma", "role": "TL", "level": "Level 1", "can_approve": 1, "is_active": 1},
    {"user_id": "tl-2", "display_name": "Yash Kalkani", "role": "TL", "level": "Level 1", "can_approve": 1, "is_active": 1},
    {"user_id": "client-1", "display_name": "Dhruv dobariya", "role": "DEVELOPER", "level": "-", "can_approve": 0, "is_active": 1},
    {"user_id": "dev-1", "display_name": "Santosh Yadav", "role": "DEVELOPER", "level": "-", "can_approve": 0, "is_active": 1},
    {"user_id": "unassigned-1", "display_name": "Random Guest", "role": "UNASSIGNED", "level": "-", "can_approve": 0, "is_active": 1},
]

def mock_get_member(user_id=None, display_name=None):
    for m in MOCK_ROSTER:
        if user_id and m["user_id"].lower() == user_id.lower():
            return m
        if display_name and m["display_name"].lower() == display_name.lower():
            return m
    return None

class TestRoleAuthorityGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.temp_dir.name, "test_role_auth.db")
        init_database(self.db_file)
        self.client = TestClient(app)

    def tearDown(self):
        close_database()
        self.temp_dir.cleanup()

    def test_is_role_authorized_helper(self):
        self.assertTrue(is_role_authorized("TL"))
        self.assertTrue(is_role_authorized("PM"))
        self.assertTrue(is_role_authorized("HM"))
        self.assertFalse(is_role_authorized("CLIENT"))
        self.assertFalse(is_role_authorized("DEVELOPER"))
        self.assertFalse(is_role_authorized("UNASSIGNED"))
        self.assertFalse(is_role_authorized(""))
        self.assertFalse(is_role_authorized(None))

    def test_is_user_authorized_approver_matrix(self):
        with patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member):
            # Authorized Management Roles
            self.assertTrue(is_user_authorized_approver(display_name="Hemil Ghori"))
            self.assertTrue(is_user_authorized_approver(display_name="Musaib Khan"))
            self.assertTrue(is_user_authorized_approver(display_name="Nishi Sharma"))
            self.assertTrue(is_user_authorized_approver(display_name="Yash Kalkani"))

            # Unauthorized Roles
            self.assertFalse(is_user_authorized_approver(display_name="Dhruv dobariya"))
            self.assertFalse(is_user_authorized_approver(display_name="Santosh Yadav"))
            self.assertFalse(is_user_authorized_approver(display_name="Random Guest"))

    def test_chat_reactions_authorization(self):
        with patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member):
            # Developer reacting with ticket emoji -> Blocked
            self.assertFalse(is_pm_approval([{"userId": "client-1", "displayName": "Dhruv dobariya", "reactionType": "🎟️"}]))
            # Developer reacting with like emoji -> Blocked
            self.assertFalse(is_pm_confirmation_approval([{"userId": "dev-1", "displayName": "Santosh Yadav", "reactionType": "like"}]))
            # Developer reacting with cross emoji -> Blocked
            self.assertFalse(is_pm_disapproval([{"userId": "client-1", "displayName": "Dhruv dobariya", "reactionType": "❌"}]))

            # TL reacting with ticket emoji -> Allowed
            self.assertTrue(is_pm_approval([{"userId": "tl-1", "displayName": "Nishi Sharma", "reactionType": "🎟️"}]))
            # PM reacting with like emoji -> Allowed
            self.assertTrue(is_pm_confirmation_approval([{"userId": "pm-1", "displayName": "Musaib Khan", "reactionType": "like"}]))
            # HM reacting with cross emoji -> Allowed
            self.assertTrue(is_pm_disapproval([{"userId": "hm-1", "displayName": "Hemil Ghori", "reactionType": "❌"}]))

    def test_developer_cannot_approve_via_get_or_post(self):
        db = get_db()
        db.execute('''
            INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'msg-dev-test',
            'dev-1',
            'Dhruv dobariya',
            'App crash when submitting payment',
            'AWAITING_FINAL_CONFIRMATION',
            json.dumps({'summary': 'Payment crash', 'suggested_assignee': 'Santosh Yadav', 'issues': [{'summary': 'Payment crash'}]})
        ))

        with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_jira:

            # 1. Developer attempts approval with reviewer query parameter -> 403 Forbidden
            res = self.client.get('/api/jira/confirm-approval/msg-dev-test?reviewer=Dhruv+dobariya')
            self.assertEqual(res.status_code, 403)
            self.assertIn('Strictly Not Permitted', res.text)
            self.assertIn('Access Denied', res.text)
            mock_jira.assert_not_called()

            # 2. Developer attempts approval via POST -> 403 Forbidden
            res_post = self.client.post('/api/jira/confirm-approval/msg-dev-test', json={'reviewer': 'Dhruv dobariya'})
            self.assertEqual(res_post.status_code, 403)
            mock_jira.assert_not_called()

            # 3. Developer attempts single-issue approval -> 403 Forbidden
            res_single = self.client.get('/api/jira/confirm-issue/msg-dev-test/0?reviewer=Santosh+Yadav')
            self.assertEqual(res_single.status_code, 403)
            mock_jira.assert_not_called()

            # 4. Anonymous call without reviewer and without reactions -> 403 Forbidden (no silent PM fallback!)
            res_anon = self.client.post('/api/jira/confirm-approval/msg-dev-test', json={})
            self.assertEqual(res_anon.status_code, 403)
            mock_jira.assert_not_called()

    def test_developer_cannot_reject_via_get_or_post(self):
        db = get_db()
        db.execute('''
            INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'msg-dev-reject',
            'dev-1',
            'Dhruv dobariya',
            'Issue text',
            'AWAITING_FINAL_CONFIRMATION',
            json.dumps({'summary': 'Issue text', 'issues': [{'summary': 'Issue text'}]})
        ))

        with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.message_service.execute_jira_ticket_decline", AsyncMock()) as mock_decline:

            # 1. Developer attempts rejection via GET -> 403 Forbidden
            res = self.client.get('/api/jira/decline-approval/msg-dev-reject?reviewer=Dhruv+dobariya')
            self.assertEqual(res.status_code, 403)
            self.assertIn('Strictly Not Permitted', res.text)
            self.assertIn('Access Denied', res.text)
            mock_decline.assert_not_called()

            # 2. Developer attempts rejection via POST -> 403 Forbidden
            res_post = self.client.post('/api/jira/decline-approval/msg-dev-reject', json={'reviewer': 'Dhruv dobariya'})
            self.assertEqual(res_post.status_code, 403)
            mock_decline.assert_not_called()

            # 3. Anonymous decline without reviewer -> 403 Forbidden
            res_anon = self.client.post('/api/jira/decline-approval/msg-dev-reject', json={})
            self.assertEqual(res_anon.status_code, 403)
            mock_decline.assert_not_called()

    def test_tl_pm_hm_can_approve_and_reject(self):
        db = get_db()
        db.execute('''
            INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'msg-mgmt-test',
            'client-1',
            'Dhruv dobariya',
            'App crash when submitting payment',
            'AWAITING_FINAL_CONFIRMATION',
            json.dumps({'summary': 'Payment crash', 'suggested_assignee': 'Santosh Yadav', 'issues': [{'summary': 'Payment crash'}]})
        ))

        with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
             patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={'success': True, 'key': 'SCRUM-501', 'url': 'http://jira/501'})), \
             patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={'success': True})):

            # TL approval succeeds
            res_tl = self.client.get('/api/jira/confirm-approval/msg-mgmt-test?reviewer=Nishi+Sharma')
            self.assertEqual(res_tl.status_code, 200)
            self.assertIn('SCRUM-501', res_tl.text)
            self.assertIn('Approved by TL Nishi Sharma', res_tl.text)
            self.assertIn('window.close()', res_tl.text)
