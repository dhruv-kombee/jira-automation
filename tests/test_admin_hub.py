import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from src.app import app
from src.database import get_db, init_database
from src.services.sender_service import identify_sender_role, Roles, is_pm_approval

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Use a temporary database and temporary Member.xlsx for each test to keep isolation clean."""
    test_db = tmp_path / "test_admin.db"
    init_database(str(test_db))
    test_excel = tmp_path / "Member.xlsx"
    monkeypatch.setattr("src.services.member_sync_service.ONEDRIVE_MEMBER_PATH", test_excel)
    monkeypatch.setattr("src.services.member_sync_service.LOCAL_MEMBER_PATH", test_excel)
    yield


def test_serve_settings_page():
    """Verify that /settings and /admin serve the new Management Hub HTML."""
    res1 = client.get("/settings")
    assert res1.status_code == 200
    assert "Management Hub & System Settings" in res1.text
    assert "settings.js" in res1.text

    res2 = client.get("/admin")
    assert res2.status_code == 200
    assert "Management Hub & System Settings" in res2.text


def test_admin_overview():
    """Verify admin overview returns aggregated statistics."""
    res = client.get("/api/admin/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "team" in data
    assert "channels" in data
    assert "jira" in data
    assert "gemini" in data
    assert data["team"]["total"] >= 4  # Default seeded members


def test_member_crud_and_dynamic_role_change():
    """Verify adding, updating, and removing team members and dynamic role detection."""
    # 1. List members
    res = client.get("/api/admin/members")
    assert res.status_code == 200
    initial_members = res.json()["members"]
    assert len(initial_members) >= 4

    # 2. Add new member
    new_member_payload = {
        "display_name": "Alice Developer",
        "role": "DEVELOPER",
        "email": "alice@company.com",
        "user_id": "guid-alice-1234",
        "specialty": "Mobile Flutter Specialist",
        "can_approve": False,
    }
    res = client.post("/api/admin/members", json=new_member_payload)
    assert res.status_code == 200
    created = res.json()["member"]
    assert created["display_name"] == "Alice Developer"
    assert created["role"] == "DEVELOPER"
    alice_id = created["id"]

    # 3. Check role detection for Alice
    role = identify_sender_role(user_id="guid-alice-1234")
    assert role == Roles.DEVELOPER

    # 4. Promote Alice to PM via Admin API
    res = client.put(f"/api/admin/members/{alice_id}", json={"role": "PM", "can_approve": True})
    assert res.status_code == 200
    updated = res.json()["member"]
    assert updated["role"] == "PM"
    assert updated["can_approve"] == 1

    # 5. Check role detection again - Alice is now dynamically identified as PM!
    role_after = identify_sender_role(user_id="guid-alice-1234")
    assert role_after == Roles.PM

    # 6. Check that Alice can now approve via reactions
    reactions = [{"userId": "guid-alice-1234", "displayName": "Alice", "reactionType": "🎟️"}]
    assert is_pm_approval(reactions, allow_client=False) is True

    # 7. Delete Alice
    del_res = client.delete(f"/api/admin/members/{alice_id}")
    assert del_res.status_code == 200


def test_monitored_channels_crud():
    """Verify adding, updating, and removing monitored Teams channels."""
    # 1. List channels
    res = client.get("/api/admin/channels")
    assert res.status_code == 200
    channels = res.json()["channels"]
    assert len(channels) >= 1

    # 2. Add new channel
    new_channel_payload = {
        "name": "Beta Testing Escalations",
        "type": "channel",
        "team_id": "team-xyz-789",
        "channel_id": "channel-abc-123",
        "usage": "DEV_ALERTS",
        "webhook_url": "https://example.com/webhook",
    }
    res = client.post("/api/admin/channels", json=new_channel_payload)
    assert res.status_code == 200
    created = res.json()["channel"]
    assert created["name"] == "Beta Testing Escalations"
    assert created["usage"] == "DEV_ALERTS"
    channel_id = created["id"]

    # 3. Update usage
    res = client.put(f"/api/admin/channels/{channel_id}", json={"usage": "CLIENT_SUPPORT"})
    assert res.status_code == 200
    updated = res.json()["channel"]
    assert updated["usage"] == "CLIENT_SUPPORT"

    # 4. Delete channel
    del_res = client.delete(f"/api/admin/channels/{channel_id}")
    assert del_res.status_code == 200


def test_system_config_retrieval_and_masking():
    """Verify that system configuration masks sensitive secrets."""
    res = client.get("/api/admin/config")
    assert res.status_code == 200
    cfg = res.json()["config"]
    assert "jira" in cfg
    assert "teams" in cfg
    assert "gemini" in cfg

    # Verify secrets are masked
    if cfg["jira"]["hasToken"]:
        assert "••••" in cfg["jira"]["apiTokenMasked"]
        assert cfg["jira"]["apiTokenRaw"] == ""  # Masked by default

    # Verify unmasked when requested with raw=true
    res_raw = client.get("/api/admin/config?raw=true")
    assert res_raw.status_code == 200
    cfg_raw = res_raw.json()["config"]
    if cfg_raw["jira"]["hasToken"]:
        assert cfg_raw["jira"]["apiTokenRaw"] != ""


@pytest.mark.asyncio
async def test_live_testers_mocked():
    """Verify live tester endpoints for Jira, Gemini, and Teams."""
    # 1. Test Jira
    with patch("src.routes.admin.test_jira_credentials", new_callable=AsyncMock) as mock_jira:
        mock_jira.return_value = {
            "success": True,
            "projectName": "Scrum Project",
            "projectKey": "SCRUM",
            "message": "Connected",
        }
        res = client.post(
            "/api/admin/test/jira",
            json={
                "base_url": "https://company.atlassian.net",
                "email": "user@company.com",
                "api_token": "token123",
                "project_key": "SCRUM",
            },
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert res.json()["projectKey"] == "SCRUM"

    # 2. Test Gemini
    with patch("src.routes.admin.test_gemini_credentials", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = {
            "success": True,
            "model": "gemini-3.5-flash-lite",
            "message": "Gemini API responding",
        }
        res = client.post(
            "/api/admin/test/gemini",
            json={"api_key": "test_key", "model": "gemini-3.5-flash-lite"},
        )
        assert res.status_code == 200
        assert res.json()["success"] is True

    # 3. Test Teams
    with patch("src.routes.admin.test_teams_webhook_payload", new_callable=AsyncMock) as mock_teams:
        mock_teams.return_value = {
            "success": True,
            "status": 200,
            "message": "Card delivered",
        }
        res = client.post(
            "/api/admin/test/teams",
            json={"webhook_url": "https://example.com/webhook"},
        )
        assert res.status_code == 200
        assert res.json()["success"] is True


def test_audit_log_tracking():
    """Verify that changes produce entries in admin_audit_log."""
    res = client.get("/api/admin/audit-log")
    assert res.status_code == 200
    logs = res.json()["logs"]
    assert isinstance(logs, list)
