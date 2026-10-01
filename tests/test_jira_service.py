import pytest
from unittest.mock import patch, MagicMock
from src.services.jira_service import text_to_adf, test_jira_connection as check_jira_conn, create_jira_issue
from src.config import config


def test_text_to_adf_conversion():
    sample_text = "Line 1: Summary\nLine 2: Details"
    adf = text_to_adf(sample_text)

    assert adf["version"] == 1
    assert adf["type"] == "doc"
    assert len(adf["content"]) == 2
    assert adf["content"][0]["content"][0]["text"] == "Line 1: Summary"


@pytest.mark.anyio
async def test_jira_connection_unconfigured():
    with patch.object(config.jira, 'base_url', ''):
        res = await check_jira_conn()
        assert res["configured"] is False
        assert res["connected"] is False


@pytest.mark.anyio
async def test_jira_connection_mocked():
    with patch.object(config.jira, 'base_url', 'https://test-company.atlassian.net'), \
         patch.object(config.jira, 'email', 'tester@example.com'), \
         patch.object(config.jira, 'api_token', 'fake-token'), \
         patch.object(config.jira, 'project_key', 'TEST'):

        mock_myself_res = MagicMock()
        mock_myself_res.status_code = 200
        mock_myself_res.json.return_value = {
            "displayName": "Test Admin",
            "emailAddress": "tester@example.com",
        }

        mock_proj_res = MagicMock()
        mock_proj_res.status_code = 200
        mock_proj_res.json.return_value = {
            "name": "Testing Project",
            "issueTypes": [{"name": "Bug"}, {"name": "Task"}],
        }

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.side_effect = [mock_myself_res, mock_proj_res]

            res = await check_jira_conn()
            assert res["connected"] is True
            assert res["user"]["name"] == "Test Admin"
            assert res["project"]["name"] == "Testing Project"
            assert "Bug" in res["project"]["issue_types"]


@pytest.mark.anyio
async def test_create_jira_issue_mocked():
    with patch.object(config.jira, 'base_url', 'https://test-company.atlassian.net'), \
         patch.object(config.jira, 'email', 'tester@example.com'), \
         patch.object(config.jira, 'api_token', 'fake-token'), \
         patch.object(config.jira, 'project_key', 'TEST'):

        mock_post_res = MagicMock()
        mock_post_res.status_code = 201
        mock_post_res.json.return_value = {
            "id": "10042",
            "key": "TEST-42",
            "self": "https://test-company.atlassian.net/rest/api/2/issue/10042",
        }

        with patch("httpx.AsyncClient.post", return_value=mock_post_res):
            res = await create_jira_issue(
                summary="[Bug] Login failed",
                description="User unable to log in",
                issue_type="Bug",
                priority="High",
            )

            assert res["success"] is True
            assert res["key"] == "TEST-42"
            assert "https://test-company.atlassian.net/browse/TEST-42" in res["url"]
