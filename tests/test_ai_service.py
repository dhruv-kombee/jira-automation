import pytest
from unittest.mock import patch, MagicMock
from src.services.ai_service import (
    extract_jira_ticket,
    _rule_based_fallback,
    GeminiKeyPool,
    ClassificationState,
    key_pool,
)
from src.config import config


@pytest.mark.anyio
async def test_extract_jira_ticket_fallback():
    # Test bug extraction in fallback
    text = "#issue found bug in the whole page of the dashboard"
    res = await extract_jira_ticket(text, sender_name="Dhruv dobariya", sender_role="CLIENT")

    assert res["is_ticket_request"] is True
    assert res["classification_state"] in [ClassificationState.CONFIRMED_ISSUE.value, ClassificationState.POSSIBLE_ISSUE.value]
    assert res["issue_type"] == "Bug"
    assert any(w in res["summary"].lower() for w in ["whole page", "dashboard", "bug"])
    assert res["priority"] in ["High", "Highest"]
    assert "teams-automation" in res["labels"]


def test_explicit_rule_based_fallback():
    res = _rule_based_fallback("#issue critical login 500 error", sender_name="Dhruv", sender_role="CLIENT")
    assert res["is_ticket_request"] is True
    assert res["classification_state"] == ClassificationState.CONFIRMED_ISSUE.value
    assert res["issue_type"] == "Bug"
    assert res["priority"] == "High"
    assert res["affected_module"] == "Backend/API"


@pytest.mark.anyio
async def test_extract_assignee_mapping():
    text = "assignee musain\n#issue please configure the webhook"
    res = await extract_jira_ticket(text, sender_name="Musaib Khan", sender_role="DEVELOPER")

    assert res["suggested_assignee"] == "Musaib Khan"
    assert any(w in (res.get("assignee_rationale") or "").lower() for w in ["mention", "direct", "musaib", "musain", "lead", "assigned", "specialist"])


@pytest.mark.anyio
async def test_non_ticket_message():
    text = "Hello everyone, hope you have a great day!"
    res = await extract_jira_ticket(text)

    assert res["is_ticket_request"] is False
    assert res["classification_state"] == ClassificationState.GENERAL_MESSAGE.value


@pytest.mark.anyio
async def test_attachment_handling_in_fallback():
    # Message with minimal text but an attached file
    attachments = [
        {
            "name": "crash_dump.log",
            "content_type": "text/plain",
            "bytes": b"Fatal error: NullPointerException at Server.java:42",
        }
    ]
    res = await extract_jira_ticket("Please investigate this", attachments=attachments)

    assert res["is_ticket_request"] is True
    assert res["classification_state"] == ClassificationState.CONFIRMED_ISSUE.value
    assert len(res["evidence"]) > 0
    assert any("nullpointer" in str(e).lower() or "attached" in str(e).lower() or "error" in str(e).lower() for e in res["evidence"])


def test_gemini_key_pool():
    pool = GeminiKeyPool()
    with patch.object(config.gemini, "api_keys", ["key-alpha", "key-beta", "key-gamma"]):
        keys = pool.get_keys()
        assert len(keys) == 3
        assert pool.get_next_key() == "key-alpha"
        assert pool.get_next_key() == "key-beta"
        assert pool.get_next_key() == "key-gamma"
        assert pool.get_next_key() == "key-alpha"  # wraps around


@pytest.mark.anyio
async def test_gemini_failover_on_429():
    with patch.object(key_pool, "get_keys", return_value=["exhausted-key", "working-key"]):
        with patch.object(key_pool, "get_next_key", side_effect=["exhausted-key", "working-key"]):
            mock_client_fail = MagicMock()
            mock_client_fail.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED rate limit exceeded")

            mock_client_ok = MagicMock()
            mock_response = MagicMock()
            mock_response.text = '''{
                "is_ticket_request": true,
                "classification_state": "CONFIRMED_ISSUE",
                "confidence": 0.98,
                "summary": "[Checkout] 504 Gateway Timeout",
                "issue_type": "Bug",
                "priority": "High",
                "priority_rationale": "Checkout is completely blocked",
                "affected_module": "Payment",
                "observed_behavior": "User gets 504 on checkout",
                "expected_behavior": "Checkout succeeds",
                "evidence": ["504 Gateway Timeout"],
                "description": "Payment gateway failing",
                "suggested_assignee": "Hemil Ghori",
                "assignee_rationale": "Backend module specialist",
                "labels": ["teams-automation", "payment"]
            }'''
            mock_client_ok.models.generate_content.return_value = mock_response

            with patch("google.genai.Client", side_effect=[mock_client_fail, mock_client_ok]):
                res = await extract_jira_ticket("#issue checkout failing with 504")

                assert res["is_ticket_request"] is True
                assert res["summary"] == "[Checkout] 504 Gateway Timeout"
                assert res["affected_module"] == "Payment"
                assert res["suggested_assignee"] == "Hemil Ghori"
                assert "gemini" in res.get("extractor", "")
