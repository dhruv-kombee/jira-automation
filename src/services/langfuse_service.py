"""Langfuse Observability & Workflow Pipeline Tracing Service.

Provides human-readable pipeline tracking for every incoming Teams message:
  Step 1: Message Reported (Reporter Name, Role, Message Snippet, Attachments)
  Step 2: Issue Classified by AI (Extracted Title, Priority, Classification, Suggested Dev)
  Step 3: Team Reaction & Response (Reactor Name, Emoji) OR SLA Follow-up Alert (TL/PM Reminder, Escalation)
  Step 4: Management Approval (Approver Name, Role, Decision, Method, Developer Assigned)
  Step 5: Jira Ticket Created (Jira Key, URL, Assignee, Attachments Uploaded)

Graceful Failover:
  If LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is not configured, or if Langfuse
  is disabled, all operations return immediately with zero performance overhead
  and zero risk to the core application.
"""
import hashlib
import json
from typing import Any, Dict, List, Optional
from src.config import config
from src.logger import logger

_langfuse_client: Optional[Any] = None
_langfuse_init_attempted: bool = False

# Active root observations indexed by message_id
_active_traces: Dict[str, Any] = {}


def _get_trace_id(message_id: str) -> str:
    """Return a deterministic 32-character hex trace ID derived from the Teams message ID."""
    clean_id = str(message_id or "").strip()
    return hashlib.md5(clean_id.encode("utf-8")).hexdigest()


def get_langfuse_client() -> Optional[Any]:
    """Retrieve or lazily initialize the Langfuse client if configured."""
    global _langfuse_client, _langfuse_init_attempted

    if _langfuse_init_attempted:
        return _langfuse_client

    _langfuse_init_attempted = True

    if not config.langfuse.is_configured:
        logger.debug("Langfuse is not configured or disabled. Pipeline tracing skipped.")
        return None

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=config.langfuse.public_key,
            secret_key=config.langfuse.secret_key,
            host=config.langfuse.host,
        )
        logger.info(
            f"Langfuse Pipeline Tracing initialized at {config.langfuse.host}",
            extra={"event": "LANGFUSE_INIT_SUCCESS"},
        )
        return _langfuse_client
    except Exception as err:
        logger.warning(
            f"Failed to initialize Langfuse client: {err}",
            extra={"event": "LANGFUSE_INIT_FAILED", "error": str(err)},
        )
        _langfuse_client = None
        return None


def _get_or_create_root_span(
    message_id: str,
    title: Optional[str] = None,
    reporter: Optional[str] = None,
    role: Optional[str] = None,
) -> Optional[Any]:
    """Get existing root observation for this message_id, or create a new one."""
    lf = get_langfuse_client()
    if not lf or not message_id:
        return None

    msg_key = str(message_id).strip()
    if msg_key in _active_traces:
        span = _active_traces[msg_key]
        if title:
            try:
                span.update(name=f"Message: {title} | Reporter: {reporter or 'Client'}")
            except Exception:
                pass
        return span

    try:
        trace_id = _get_trace_id(msg_key)
        trace_name = (
            f"Message: {title} | Reporter: {reporter or 'User'}"
            if title
            else f"Message ID: {msg_key} | Reporter: {reporter or 'User'}"
        )

        root_span = lf.start_observation(
            trace_context={"trace_id": trace_id},
            name=trace_name,
            metadata={
                "message_id": msg_key,
                "reporter_name": reporter or "Unknown",
                "reporter_role": role or "Unknown",
                "source": "Microsoft Teams",
            },
        )
        _active_traces[msg_key] = root_span
        return root_span
    except Exception as err:
        logger.debug(f"Langfuse root span creation error: {err}")
        return None


def log_step1_message_reported(
    message_id: str,
    reporter_name: str,
    reporter_role: str,
    text: str,
    attachments_count: int = 0,
    chat_id: Optional[str] = None,
) -> None:
    """Step 1 in Pipeline: Inbound message received and recorded."""
    try:
        root = _get_or_create_root_span(
            message_id=message_id,
            reporter=reporter_name,
            role=reporter_role,
        )
        if not root:
            return

        preview = (text or "").strip()
        if len(preview) > 300:
            preview = preview[:297] + "..."

        root.create_event(
            name="Step 1: Message Reported",
            input={
                "reporter_name": reporter_name or "Client",
                "reporter_role": reporter_role or "CLIENT",
                "message_snippet": preview,
                "attachments_count": attachments_count,
                "chat_or_channel_id": chat_id or "Teams Chat",
            },
            output={
                "status": "Message Received & Queued for AI Triage",
                "message_id": str(message_id),
            },
            metadata={
                "step": 1,
                "action": "MESSAGE_RECEIVED",
            },
        )
    except Exception as err:
        logger.debug(f"Langfuse Step 1 error: {err}")


def log_step2_ai_classified(
    message_id: str,
    classification: str,
    title: str,
    priority: str,
    suggested_dev: Optional[str] = None,
    affected_module: Optional[str] = None,
    reporter_name: Optional[str] = None,
    missing_details: Optional[List[str]] = None,
) -> None:
    """Step 2 in Pipeline: Gemini AI classified message and extracted issue spec."""
    try:
        root = _get_or_create_root_span(
            message_id=message_id,
            title=title,
            reporter=reporter_name,
        )
        if not root:
            return

        # Update root name with the extracted title
        try:
            root.update(name=f"[{priority}] {title} | Reporter: {reporter_name or 'Client'}")
        except Exception:
            pass

        root.create_event(
            name="Step 2: Issue Classified by AI",
            input={
                "classification_result": classification,
            },
            output={
                "issue_title": title,
                "priority": priority,
                "suggested_developer": suggested_dev or "Unassigned",
                "affected_module": affected_module or "General",
                "missing_details": missing_details or [],
                "next_step": (
                    "Interactive Triage Card posted to Teams"
                    if classification in ("CONFIRMED_ISSUE", "POSSIBLE_ISSUE")
                    else ("Clarification requested from reporter" if classification == "INCOMPLETE_ISSUE" else "No action needed")
                ),
            },
            metadata={
                "step": 2,
                "classification": classification,
                "priority": priority,
            },
        )
    except Exception as err:
        logger.debug(f"Langfuse Step 2 error: {err}")


def log_step3_team_reaction(
    message_id: str,
    reactor_name: str,
    reactor_role: str,
    emoji: str,
) -> None:
    """Step 3 in Pipeline (Scenario A): A team member or manager reacted to the message."""
    try:
        root = _get_or_create_root_span(message_id=message_id)
        if not root:
            return

        root.create_event(
            name="Step 3: Team Reaction Received",
            input={
                "reactor_name": reactor_name,
                "reactor_role": reactor_role,
                "reaction_emoji": emoji,
            },
            output={
                "status": f"{reactor_name} ({reactor_role}) reacted with {emoji}",
                "sla_halted": True,
            },
            metadata={
                "step": 3,
                "action": "REACTION_ACKNOWLEDGED",
                "emoji": emoji,
            },
        )
    except Exception as err:
        logger.debug(f"Langfuse Step 3 reaction error: {err}")


def log_step3_sla_followup(
    message_id: str,
    stage: str,
    target_name: str,
    target_role: str,
    channel: str = "Teams Card",
    elapsed_minutes: Optional[int] = None,
) -> None:
    """Step 3 in Pipeline (Scenario B): No reaction received; SLA escalation triggered."""
    try:
        root = _get_or_create_root_span(message_id=message_id)
        if not root:
            return

        root.create_event(
            name=f"Step 3: SLA Alert Triggered ({stage})",
            input={
                "alert_stage": stage,
                "elapsed_minutes": elapsed_minutes or 0,
                "target_recipient": f"{target_name} ({target_role})",
                "delivery_channel": channel,
            },
            output={
                "status": f"No reaction after {elapsed_minutes}m. Follow-up dispatched to {target_name} via {channel}.",
            },
            metadata={
                "step": 3,
                "action": "SLA_REMINDER_SENT",
                "stage": stage,
            },
        )
    except Exception as err:
        logger.debug(f"Langfuse Step 3 SLA error: {err}")


def log_step4_management_approval(
    message_id: str,
    approver_name: str,
    approver_role: str,
    decision: str = "APPROVED",
    method: str = "Teams Triage Card Button",
    assigned_dev: Optional[str] = None,
) -> None:
    """Step 4 in Pipeline: Authorized manager approved or declined the issue."""
    try:
        root = _get_or_create_root_span(message_id=message_id)
        if not root:
            return

        status_text = (
            f"Approved by {approver_name} ({approver_role}) via {method}"
            if decision == "APPROVED"
            else f"Declined by {approver_name} ({approver_role}) via {method}"
        )

        root.create_event(
            name="Step 4: Management Approval Decision",
            input={
                "reviewer_name": approver_name,
                "reviewer_role": approver_role,
                "decision": decision,
                "approval_method": method,
                "assigned_developer": assigned_dev or "Unchanged",
            },
            output={
                "status": status_text,
                "action_taken": "Creating Jira Ticket" if decision == "APPROVED" else "Closed as Declined",
            },
            metadata={
                "step": 4,
                "decision": decision,
                "method": method,
            },
        )
    except Exception as err:
        logger.debug(f"Langfuse Step 4 error: {err}")


def log_step5_jira_created(
    message_id: str,
    jira_key: str,
    jira_url: str,
    assignee_name: Optional[str] = None,
    attachments_uploaded: int = 0,
) -> None:
    """Step 5 in Pipeline: Jira issue created and confirmation posted to Teams."""
    try:
        root = _get_or_create_root_span(message_id=message_id)
        if not root:
            return

        root.create_event(
            name="Step 5: Jira Ticket Created",
            input={
                "target_project": jira_key.split("-")[0] if "-" in jira_key else jira_key,
                "assigned_developer": assignee_name or "Unassigned",
                "attachments_uploaded": attachments_uploaded,
            },
            output={
                "jira_ticket_key": jira_key,
                "jira_ticket_url": jira_url,
                "status": "Ticket Active & Confirmed in Teams Chat",
            },
            metadata={
                "step": 5,
                "jira_key": jira_key,
                "jira_url": jira_url,
            },
        )

        # Mark root span as successfully completed
        try:
            root.update(
                output={
                    "final_status": "COMPLETED",
                    "jira_key": jira_key,
                    "jira_url": jira_url,
                }
            )
            root.end()
        except Exception:
            pass

        # Cleanup memory for completed message
        msg_key = str(message_id).strip()
        _active_traces.pop(msg_key, None)
    except Exception as err:
        logger.debug(f"Langfuse Step 5 error: {err}")


def log_issue_declined(
    message_id: str,
    decliner_name: str,
    decliner_role: str,
    reason: Optional[str] = None,
) -> None:
    """Log when an issue is explicitly declined by management."""
    try:
        root = _get_or_create_root_span(message_id=message_id)
        if not root:
            return

        root.create_event(
            name="Step 4: Issue Declined by Management",
            input={
                "declined_by": f"{decliner_name} ({decliner_role})",
                "decline_reason": reason or "Declined by reviewer",
            },
            output={
                "status": "Issue Declined — No Jira Ticket Created",
            },
            metadata={
                "step": 4,
                "decision": "DECLINED",
            },
        )

        try:
            root.update(output={"final_status": "DECLINED"})
            root.end()
        except Exception:
            pass

        msg_key = str(message_id).strip()
        _active_traces.pop(msg_key, None)
    except Exception as err:
        logger.debug(f"Langfuse decline error: {err}")


def flush_langfuse() -> None:
    """Flush pending Langfuse events to server."""
    lf = get_langfuse_client()
    if lf:
        try:
            lf.flush()
        except Exception:
            pass
