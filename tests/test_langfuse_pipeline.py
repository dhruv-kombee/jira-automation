import pytest
from unittest.mock import MagicMock, patch
from src.services.langfuse_service import (
    log_step1_message_reported,
    log_step2_ai_classified,
    log_step3_team_reaction,
    log_step3_sla_followup,
    log_step4_management_approval,
    log_step5_jira_created,
    log_issue_declined,
    _get_trace_id,
)
from src.config import config


def test_trace_id_deterministic():
    """Verify trace_id generation is deterministic and 32 characters."""
    t1 = _get_trace_id("1730192837482")
    t2 = _get_trace_id("1730192837482")
    assert t1 == t2
    assert len(t1) == 32


@patch("src.services.langfuse_service.get_langfuse_client")
def test_full_pipeline_tracing_mocked(mock_get_client):
    """Verify that all 5 pipeline steps record events correctly on the mock Langfuse client."""
    mock_lf = MagicMock()
    mock_root = MagicMock()
    mock_lf.start_observation.return_value = mock_root
    mock_get_client.return_value = mock_lf

    msg_id = "test_msg_999"

    # Step 1: Inbound Message
    log_step1_message_reported(
        message_id=msg_id,
        reporter_name="Dhruv",
        reporter_role="CLIENT",
        text="The checkout button gives 500 error",
        attachments_count=1,
    )
    assert mock_lf.start_observation.called
    assert mock_root.create_event.called

    # Step 2: AI Classified
    log_step2_ai_classified(
        message_id=msg_id,
        classification="CONFIRMED_ISSUE",
        title="[Checkout] 500 error on button",
        priority="High",
        suggested_dev="Alex",
        reporter_name="Dhruv",
    )

    # Step 3: Team Reaction
    log_step3_team_reaction(
        message_id=msg_id,
        reactor_name="Sarah",
        reactor_role="TL",
        emoji="thumbsup",
    )

    # Step 3 (Alternative): SLA Followup
    log_step3_sla_followup(
        message_id=msg_id,
        stage="Stage 1: Teams Follow-up to TL",
        target_name="Sarah",
        target_role="TL",
        channel="Teams Adaptive Card",
        elapsed_minutes=10,
    )

    # Step 4: Management Approval
    log_step4_management_approval(
        message_id=msg_id,
        approver_name="Alice",
        approver_role="PM",
        decision="APPROVED",
        assigned_dev="Alex",
    )

    # Step 5: Jira Ticket Created
    log_step5_jira_created(
        message_id=msg_id,
        jira_key="SCRUM-105",
        jira_url="https://jira.example.com/browse/SCRUM-105",
        assignee_name="Alex",
        attachments_uploaded=1,
    )

    # Total events created across pipeline
    assert mock_root.create_event.call_count >= 5
    assert mock_root.end.called


def test_graceful_when_disabled():
    """Verify all pipeline methods run without raising any exceptions when Langfuse is unconfigured."""
    with patch.object(config.langfuse, "enabled", False):
        msg_id = "unconfigured_msg"
        log_step1_message_reported(msg_id, "User", "CLIENT", "Text")
        log_step2_ai_classified(msg_id, "CONFIRMED_ISSUE", "Title", "High")
        log_step3_team_reaction(msg_id, "TL", "TL", "👍")
        log_step3_sla_followup(msg_id, "Stage 1", "TL", "TL")
        log_step4_management_approval(msg_id, "PM", "PM")
        log_step5_jira_created(msg_id, "KEY-1", "url")
        log_issue_declined(msg_id, "PM", "PM")
