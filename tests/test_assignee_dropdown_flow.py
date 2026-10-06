import sys
sys.path.insert(0, '.')
import json
import pathlib
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from src.app import app
from src.database import init_database, close_database, get_db

db_path = './data/test_assignee_dropdown.db'
init_database(db_path)
db = get_db()
db.execute("DELETE FROM messages WHERE message_id = 'test-msg-dropdown-1'")
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

# 1. Test GET /api/jira/confirm-approval/{message_id} returns dropdown modal HTML
res = client.get('/api/jira/confirm-approval/test-msg-dropdown-1')
assert res.status_code == 200
assert 'assigneeSelect' in res.text
assert 'Santosh Yadav' in res.text
assert 'Musaib Khan' in res.text
assert 'Nishi Sharma' in res.text
assert 'Hemil Ghori' in res.text
print('Step 1: Dropdown HTML test PASSED')

# 2. Test GET with auto=1 and assignee override (Musaib Khan)
with patch('src.services.jira_service.create_jira_issue', AsyncMock(return_value={'success': True, 'key': 'SCRUM-99', 'url': 'http://jira/SCRUM-99'})) as mock_create, \
     patch('src.services.teams_notifier.send_ticket_created_notification', AsyncMock(return_value={'success': True})):
    res_auto = client.get('/api/jira/confirm-approval/test-msg-dropdown-1?assignee=Musaib+Khan&auto=1')
    assert res_auto.status_code == 200
    assert 'SCRUM-99' in res_auto.text
    assert mock_create.call_args.kwargs['assignee_name'] == 'Musaib Khan'
    print('Step 2: Auto approval with assignee override (Musaib Khan) PASSED')

# 3. Test POST form submission with assignee override (Nishi Sharma)
db.execute("UPDATE messages SET confirmation_status = 'AWAITING_FINAL_CONFIRMATION', jira_issue_key = NULL WHERE message_id = 'test-msg-dropdown-1'")
with patch('src.services.jira_service.create_jira_issue', AsyncMock(return_value={'success': True, 'key': 'SCRUM-100', 'url': 'http://jira/SCRUM-100'})) as mock_post, \
     patch('src.services.teams_notifier.send_ticket_created_notification', AsyncMock(return_value={'success': True})):
    res_post = client.post('/api/jira/confirm-approval/test-msg-dropdown-1', data={'assignee': 'Nishi Sharma'})
    assert res_post.status_code == 200
    assert 'SCRUM-100' in res_post.text
    assert mock_post.call_args.kwargs['assignee_name'] == 'Nishi Sharma'
    print('Step 3: POST form submission with assignee override (Nishi Sharma) PASSED')

close_database()
pathlib.Path(db_path).unlink(missing_ok=True)
print('ALL DROPDOWN VERIFICATION TESTS PASSED!')
