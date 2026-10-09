import sys
sys.path.insert(0, '.')
import json
import pytest
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

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_role_authority.db"
    init_database(str(db_file))
    yield
    close_database()

MOCK_ROSTER = [
    {"user_id": "hm-1", "display_name": "Hemil Ghori", "role": "HM", "level": "Level 3", "can_approve": 1, "is_active": 1},
    {"user_id": "pm-1", "display_name": "Musaib Khan", "role": "PM", "level": "Level 2", "can_approve": 1, "is_active": 1},
    {"user_id": "tl-1", "display_name": "Nishi Sharma", "role": "TL", "level": "Level 1", "can_approve": 1, "is_active": 1},
    {"user_id": "tl-2", "display_name": "Yash Kalkani", "role": "TL", "level": "Level 1", "can_approve": 1, "is_active": 1},
    {"user_id": "client-1", "display_name": "Dhruv dobariya", "role": "CLIENT", "level": "-", "can_approve": 0, "is_active": 1},
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

def test_is_role_authorized_helper():
    assert is_role_authorized("TL") is True
    assert is_role_authorized("PM") is True
    assert is_role_authorized("HM") is True
    assert is_role_authorized("CLIENT") is False
    assert is_role_authorized("DEVELOPER") is False
    assert is_role_authorized("UNASSIGNED") is False
    assert is_role_authorized("") is False
    assert is_role_authorized(None) is False

def test_is_user_authorized_approver_matrix():
    with patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member):
        # Authorized Management Roles
        assert is_user_authorized_approver(display_name="Hemil Ghori") is True
        assert is_user_authorized_approver(display_name="Musaib Khan") is True
        assert is_user_authorized_approver(display_name="Nishi Sharma") is True
        assert is_user_authorized_approver(display_name="Yash Kalkani") is True

        # Unauthorized Roles
        assert is_user_authorized_approver(display_name="Dhruv dobariya") is False
        assert is_user_authorized_approver(display_name="Santosh Yadav") is False
        assert is_user_authorized_approver(display_name="Random Guest") is False

def test_chat_reactions_authorization():
    with patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member):
        # Client reacting with ticket emoji -> Blocked
        assert is_pm_approval([{"userId": "client-1", "displayName": "Dhruv dobariya", "reactionType": "🎟️"}]) is False
        # Developer reacting with like emoji -> Blocked
        assert is_pm_confirmation_approval([{"userId": "dev-1", "displayName": "Santosh Yadav", "reactionType": "like"}]) is False
        # Client reacting with cross emoji -> Blocked
        assert is_pm_disapproval([{"userId": "client-1", "displayName": "Dhruv dobariya", "reactionType": "❌"}]) is False

        # TL reacting with ticket emoji -> Allowed
        assert is_pm_approval([{"userId": "tl-1", "displayName": "Nishi Sharma", "reactionType": "🎟️"}]) is True
        # PM reacting with like emoji -> Allowed
        assert is_pm_confirmation_approval([{"userId": "pm-1", "displayName": "Musaib Khan", "reactionType": "like"}]) is True
        # HM reacting with cross emoji -> Allowed
        assert is_pm_disapproval([{"userId": "hm-1", "displayName": "Hemil Ghori", "reactionType": "❌"}]) is True

def test_client_cannot_approve_via_get_endpoint():
    db = get_db()
    db.execute('''
        INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        'msg-client-test',
        'client-1',
        'Dhruv dobariya',
        'App crash when submitting payment',
        'AWAITING_FINAL_CONFIRMATION',
        json.dumps({'summary': 'Payment crash', 'suggested_assignee': 'Santosh Yadav'})
    ))

    client = TestClient(app)
    with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.jira_service.create_jira_issue", AsyncMock()) as mock_jira:

        # 1. Client attempts approval with reviewer query parameter -> 403 Forbidden
        res = client.get('/api/jira/confirm-approval/msg-client-test?reviewer=Dhruv+dobariya')
        assert res.status_code == 403
        assert 'No Authority to Confirm' in res.text
        assert 'Clients, Developers, and Unassigned roles do not have authority' in res.text
        mock_jira.assert_not_called()

        # 2. Developer attempts approval -> 403 Forbidden
        res_dev = client.get('/api/jira/confirm-approval/msg-client-test?reviewer=Santosh+Yadav')
        assert res_dev.status_code == 403
        assert 'No Authority to Confirm' in res_dev.text
        mock_jira.assert_not_called()

        # 3. Client attempts approval via cookie -> 403 Forbidden
        client.cookies.set("jira_reviewer", "Dhruv dobariya")
        res_cookie = client.get('/api/jira/confirm-approval/msg-client-test')
        assert res_cookie.status_code == 403
        assert 'No Authority to Confirm' in res_cookie.text
        mock_jira.assert_not_called()

def test_client_cannot_reject_via_get_endpoint():
    db = get_db()
    db.execute('''
        INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        'msg-client-reject',
        'client-1',
        'Dhruv dobariya',
        'Issue text',
        'AWAITING_FINAL_CONFIRMATION',
        json.dumps({'summary': 'Issue text'})
    ))

    client = TestClient(app)
    with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.message_service.execute_jira_ticket_decline", AsyncMock()) as mock_decline:

        # Client attempts rejection -> 403 Forbidden
        res = client.get('/api/jira/decline-approval/msg-client-reject?reviewer=Dhruv+dobariya')
        assert res.status_code == 403
        assert 'No Authority to Reject' in res.text
        mock_decline.assert_not_called()

def test_tl_pm_hm_can_approve_and_reject():
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
        json.dumps({'summary': 'Payment crash', 'suggested_assignee': 'Santosh Yadav'})
    ))

    client = TestClient(app)
    with patch("src.services.member_sync_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.sender_service.get_member_by_id_or_name", side_effect=mock_get_member), \
         patch("src.services.jira_service.create_jira_issue", AsyncMock(return_value={'success': True, 'key': 'SCRUM-501', 'url': 'http://jira/501'})), \
         patch("src.services.teams_notifier.send_ticket_created_notification", AsyncMock(return_value={'success': True})):

        # TL approval succeeds
        res_tl = client.get('/api/jira/confirm-approval/msg-mgmt-test?reviewer=Nishi+Sharma')
        assert res_tl.status_code == 200
        assert 'SCRUM-501' in res_tl.text
        assert 'Approved by TL Nishi Sharma' in res_tl.text
        assert 'window.close()' in res_tl.text
