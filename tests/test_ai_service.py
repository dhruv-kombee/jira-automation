import pytest
from src.services.ai_service import extract_jira_ticket, _rule_based_fallback


@pytest.mark.anyio
async def test_extract_jira_ticket_fallback():
    # Test bug extraction
    text = "#issue found bug in the whole page of the dashboard"
    res = await extract_jira_ticket(text, sender_name="Dhruv dobariya", sender_role="CLIENT")

    assert res["is_ticket_request"] is True
    assert res["issue_type"] == "Bug"
    assert "whole page" in res["summary"].lower()
    assert res["priority"] in ["High", "Highest"]
    assert "teams-automation" in res["labels"]


@pytest.mark.anyio
async def test_extract_assignee_mapping():
    text = "assignee musain\n#issue please configure the webhook"
    res = await extract_jira_ticket(text, sender_name="Musaib Khan", sender_role="DEVELOPER")

    assert res["is_ticket_request"] is True
    assert res["suggested_assignee"] == "Musaib Khan"


@pytest.mark.anyio
async def test_non_ticket_message():
    text = "Hello everyone, hope you have a great day!"
    res = await extract_jira_ticket(text)

    assert res["is_ticket_request"] is False
