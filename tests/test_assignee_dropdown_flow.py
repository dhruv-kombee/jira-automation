import sys
sys.path.insert(0, '.')
import json
import pathlib
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.app import app
from src.database import init_database, close_database, get_db

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    db_file = tmp_path / "test_assignee_dropdown.db"
    init_database(str(db_file))
    yield
    close_database()

def test_assignee_dropdown_get_direct_creation():
    db = get_db()
    db.execute('''
        INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        'test-msg-dropdown-1',
        'client-1',
        'Dhruv dobariya',
        'Checkout button fails with 500 error',
        'AWAITING_FINAL_CONFIRMATION',
        json.dumps({
            'summary': 'Checkout 500 error',
            'suggested_assignee': 'Santosh Yadav',
            'issues': [{'summary': 'Checkout 500 error', 'suggested_assignee': 'Santosh Yadav'}]
        })
    ))
    client = TestClient(app)
    with patch('src.services.jira_service.create_jira_issue', AsyncMock(return_value={'success': True, 'key': 'SCRUM-98', 'url': 'http://jira/SCRUM-98'})), \
         patch('src.services.teams_notifier.send_ticket_created_notification', AsyncMock(return_value={'success': True})):
        res = client.get('/api/jira/confirm-approval/test-msg-dropdown-1')
        assert res.status_code == 200
        assert 'SCRUM-98' in res.text
        assert 'assigneeSelect' not in res.text
        assert 'window.close()' in res.text


def test_assignee_dropdown_auto_approval():
    db = get_db()
    db.execute('''
        INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        'test-msg-dropdown-auto',
        'client-1',
        'Dhruv dobariya',
        'Checkout button fails with 500 error',
        'AWAITING_FINAL_CONFIRMATION',
        json.dumps({
            'summary': 'Checkout 500 error',
            'suggested_assignee': 'Santosh Yadav',
            'issues': [{'summary': 'Checkout 500 error', 'suggested_assignee': 'Santosh Yadav'}]
        })
    ))
    client = TestClient(app)
    with patch('src.services.jira_service.create_jira_issue', AsyncMock(return_value={'success': True, 'key': 'SCRUM-99', 'url': 'http://jira/SCRUM-99'})) as mock_create, \
         patch('src.services.teams_notifier.send_ticket_created_notification', AsyncMock(return_value={'success': True})):
        res_auto = client.get('/api/jira/confirm-approval/test-msg-dropdown-auto?assignee=Musaib+Khan&auto=1')
        assert res_auto.status_code == 200
        assert 'SCRUM-99' in res_auto.text
        assert mock_create.call_args.kwargs['assignee_name'] == 'Musaib Khan'


def test_assignee_dropdown_post_form():
    db = get_db()
    db.execute('''
        INSERT INTO messages (message_id, sender_user_id, sender_display_name, message_text, confirmation_status, ai_ticket)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        'test-msg-dropdown-post',
        'client-1',
        'Dhruv dobariya',
        'Checkout button fails with 500 error',
        'AWAITING_FINAL_CONFIRMATION',
        json.dumps({
            'summary': 'Checkout 500 error',
            'suggested_assignee': 'Santosh Yadav',
            'issues': [{'summary': 'Checkout 500 error', 'suggested_assignee': 'Santosh Yadav'}]
        })
    ))
    client = TestClient(app)
    with patch('src.services.jira_service.create_jira_issue', AsyncMock(return_value={'success': True, 'key': 'SCRUM-100', 'url': 'http://jira/SCRUM-100'})) as mock_post, \
         patch('src.services.teams_notifier.send_ticket_created_notification', AsyncMock(return_value={'success': True})):
        res_post = client.post('/api/jira/confirm-approval/test-msg-dropdown-post', data={'assignee': 'Nishi Sharma', 'reviewer': 'Musaib Khan'})
        assert res_post.status_code == 200
        assert 'SCRUM-100' in res_post.text
        assert mock_post.call_args.kwargs['assignee_name'] == 'Nishi Sharma'
