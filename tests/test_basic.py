import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from src.database import init_database, close_database
from src.repositories.message_repository import store_message, find_message, get_all_messages
from src.services.sender_service import identify_sender_role, Roles
from src.services.message_service import strip_html, parse_resource_path, normalize_message
from src.app import app


@pytest.fixture(autouse=True)
def setup_teardown():
    test_db = Path("./data/test_messages.db")
    close_database()
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass
    db = init_database(str(test_db))
    db.execute("DELETE FROM messages;")
    yield
    close_database()
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


def test_database_crud():
    test_msg = {
        "messageId": "msg-001",
        "teamId": "team-001",
        "channelId": "chan-001",
        "sender": {"userId": "user-client-123", "displayName": "Alice Client"},
        "message": {
            "text": "Please fix this high priority bug",
            "createdAt": "2026-10-01T09:00:00Z",
            "modifiedAt": None,
            "webUrl": "https://teams.microsoft.com/l/message/...",
        },
        "replyToId": None,
        "attachments": [{"id": "att-1", "name": "screenshot.png", "contentType": "image/png"}],
    }

    # First insert
    res = store_message(test_msg)
    assert res["stored"] is True
    assert res["duplicate"] is False
    assert res["record"]["id"] is not None

    # Duplicate insert
    dup = store_message(test_msg)
    assert dup["stored"] is False
    assert dup["duplicate"] is True

    # Find message
    found = find_message("team-001", "chan-001", "msg-001")
    assert found is not None
    assert found["sender_display_name"] == "Alice Client"
    assert found["message_text"] == "Please fix this high priority bug"

    # Get all messages
    all_msgs = get_all_messages(10)
    assert len(all_msgs) == 1
    assert all_msgs[0]["message_id"] == "msg-001"


def test_parse_resource_path():
    # Top-level message
    res1 = parse_resource_path("teams('team-1')/channels('channel-1')/messages('msg-1')")
    assert res1 is not None
    assert res1["team_id"] == "team-1"
    assert res1["channel_id"] == "channel-1"
    assert res1["message_id"] == "msg-1"
    assert res1["parent_message_id"] is None

    # Reply message
    res2 = parse_resource_path("teams('team-1')/channels('channel-1')/messages('msg-1')/replies('reply-1')")
    assert res2 is not None
    assert res2["type"] == "channel"
    assert res2["team_id"] == "team-1"
    assert res2["channel_id"] == "channel-1"
    assert res2["message_id"] == "reply-1"
    assert res2["parent_message_id"] == "msg-1"
    assert res2["reply_message_id"] == "reply-1"

    # Group chat message
    res3 = parse_resource_path("chats('19:c8c7d01f1cc24db4b04de10e93c865de@thread.v2')/messages('12345')")
    assert res3 is not None
    assert res3["type"] == "chat"
    assert res3["chat_id"] == "19:c8c7d01f1cc24db4b04de10e93c865de@thread.v2"
    assert res3["message_id"] == "12345"


def test_strip_html():
    raw_html = "<div><p>Hello <b>World</b><br/>Next line &amp; &lt;tag&gt;</p></div>"
    cleaned = strip_html(raw_html)
    assert "Hello World" in cleaned
    assert "Next line & <tag>" in cleaned
    assert "<p>" not in cleaned


def test_fastapi_endpoints():
    client = TestClient(app)

    # Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"
    assert res_health.json()["service"] == "teams-mvp"

    # Webhook validation GET
    token_get = client.get("/webhooks/teams?validationToken=my_secret_token_123")
    assert token_get.status_code == 200
    assert token_get.text == "my_secret_token_123"

    # Webhook validation POST
    token_post = client.post("/webhooks/teams?validationToken=post_token_456")
    assert token_post.status_code == 200
    assert token_post.text == "post_token_456"

    # Webhook invalid payload
    bad_payload = client.post("/webhooks/teams", json={"not_value": 123})
    assert bad_payload.status_code == 400

    # Webhook valid notification payload (202 Accepted)
    valid_payload = client.post(
        "/webhooks/teams",
        json={"value": [{"subscriptionId": "sub-1", "resource": "teams('t')/channels('c')/messages('m')"}]},
    )
    assert valid_payload.status_code == 202
    assert valid_payload.json() == {"status": "accepted"}
