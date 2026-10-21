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


def test_strip_markdown_asterisks():
    from src.services.jira_service import strip_markdown_asterisks
    assert strip_markdown_asterisks("**Reported By**: Dhruv") == "Reported By: Dhruv"
    assert strip_markdown_asterisks("*Observed Behavior*: Button fails") == "Observed Behavior: Button fails"
    assert strip_markdown_asterisks("* List item") == "List item"
    assert strip_markdown_asterisks("__bold text__") == "bold text"


def test_build_jira_adf_zero_asterisks():
    import json
    from src.services.jira_service import build_jira_adf
    adf = build_jira_adf(
        summary="[Dashboard] Refresh button not working",
        priority="Medium",
        reporter_name="Dhruv dobariya",
        reporter_role="CLIENT",
        module="Dashboard / Navigation",
        observed_behavior="User clicks refresh but nothing happens",
        expected_behavior="Data reloads and view refreshes",
        steps_to_reproduce=["Go to dashboard", "Click refresh in top right", "Notice lack of response"],
        evidence=["User reported button unresponsive", "Screenshot of refresh icon"],
        acceptance_criteria=["Clicking refresh updates stats", "Spinner confirms operation"],
        suggested_assignee="Musaib Khan",
    )

    raw_json = json.dumps(adf)
    assert "**" not in raw_json
    assert "*" not in raw_json
    assert adf["version"] == 1
    assert adf["type"] == "doc"
    # Check that panel, headings, and bullet lists exist
    types = [node["type"] for node in adf["content"]]
    assert "panel" in types
    assert "heading" in types
    assert "bulletList" in types


def test_text_to_adf_strips_asterisks():
    import json
    from src.services.jira_service import text_to_adf
    sample = """*Reported By*: Dhruv dobariya (CLIENT)
*Observed Behavior*: The button is unresponsive.
*Evidence*:
• User feedback stating the button is unresponsive
• Screenshot showing the icon"""
    adf = text_to_adf(sample)
    raw_json = json.dumps(adf)
    assert "**" not in raw_json
    assert "*" not in raw_json
    types = [node["type"] for node in adf["content"]]
    assert "heading" in types
    assert "bulletList" in types


@pytest.mark.anyio
async def test_create_jira_issue_dynamic_reporter():
    from src.services.jira_service import create_jira_issue
    with patch.object(config.jira, 'base_url', 'https://test-company.atlassian.net'), \
         patch.object(config.jira, 'email', 'dhruv.d.kombee@gmail.com'), \
         patch.object(config.jira, 'api_token', 'fake-token'), \
         patch.object(config.jira, 'project_key', 'SCRUM'), \
         patch("src.services.jira_service.resolve_jira_assignee") as mock_resolve:

        # Let mock_resolve return a specific account ID for the reporter
        mock_resolve.side_effect = lambda query, pk: "712020:test-reporter-id" if "Nishi" in query else None

        mock_post_res = MagicMock()
        mock_post_res.status_code = 201
        mock_post_res.json.return_value = {
            "id": "10099",
            "key": "SCRUM-99",
            "self": "https://test-company.atlassian.net/rest/api/3/issue/10099",
        }

        with patch("httpx.AsyncClient.post", return_value=mock_post_res) as mock_post:
            res = await create_jira_issue(
                summary="[UI] Modal dismiss button fails",
                description="Clicking close does nothing",
                reporter_name="Nishi Sharma",
                ticket_data={"reporter_name": "Nishi Sharma", "approved_by": "PM Musaib Khan"},
            )

            assert res["success"] is True
            assert res["key"] == "SCRUM-99"

            # Verify that POST payload included the resolved reporter accountId
            call_kwargs = mock_post.call_args.kwargs
            sent_payload = call_kwargs.get("json")
            assert sent_payload is not None
            assert sent_payload["fields"]["reporter"] == {"accountId": "712020:test-reporter-id"}


def test_build_jira_adf_with_approved_by():
    import json
    from src.services.jira_service import build_jira_adf
    adf = build_jira_adf(
        summary="Test Ticket",
        reporter_name="Santosh Yadav",
        reporter_role="CLIENT",
        approved_by="PM Musaib Khan",
    )
    raw = json.dumps(adf)
    assert "Reporter:" in raw
    assert "Santosh Yadav (CLIENT)" in raw
    assert "Approved By:" in raw
    assert "PM Musaib Khan" in raw


