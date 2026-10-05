import httpx
from datetime import datetime
from typing import Optional, Dict, Any
from src.config import config
from src.logger import logger


def get_current_timestamp_str() -> str:
    """Return formatted local timestamp with timezone/offset."""
    try:
        now = datetime.now().astimezone()
        return now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_adaptive_card_payload(
    ticket_key: str,
    ticket_url: str,
    summary: str,
    issue_type: str,
    priority: str,
    created_at: str,
    assignee: str,
    reporter: str,
    approval_note: str,
    module: Optional[str] = None,
    evidence: Optional[list] = None,
) -> Dict[str, Any]:
    """Build a Teams-compatible Adaptive Card payload."""
    clean_summary = summary.replace("\n", " ").strip()
    plain_text = (
        f"🎟️ Jira Ticket Created: {ticket_key} - {clean_summary}\n"
        f"Link: {ticket_url}\n"
        f"Date: {created_at}\n"
        f"Assignee: {assignee}\n"
        f"Approval: {approval_note}"
    )

    facts = [
        {"title": "Ticket:", "value": f"[{ticket_key}]({ticket_url})"},
        {"title": "Summary:", "value": clean_summary},
        {"title": "Type & Priority:", "value": f"{issue_type} | {priority}"},
    ]
    if module and module != "General":
        facts.append({"title": "Module:", "value": module})
    if evidence and len(evidence) > 0:
        ev_text = "; ".join(str(e) for e in evidence[:2])
        if len(ev_text) > 80:
            ev_text = ev_text[:77] + "..."
        facts.append({"title": "Evidence:", "value": ev_text})

    facts.extend([
        {"title": "Date & Time:", "value": created_at},
        {"title": "Assignee:", "value": assignee},
        {"title": "Reporter:", "value": reporter},
        {"title": "Approval:", "value": approval_note},
    ])

    return {
        "type": "message",
        "text": plain_text,
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "Container",
                            "style": "emphasis",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "🎟️ Jira Ticket Created",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Good",
                                }
                            ],
                        },
                        {
                            "type": "FactSet",
                            "facts": facts,
                        },
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open in Jira ↗",
                            "url": ticket_url,
                        }
                    ],
                },
            }
        ],
    }


def build_html_message(
    ticket_key: str,
    ticket_url: str,
    summary: str,
    issue_type: str,
    priority: str,
    created_at: str,
    assignee: str,
    reporter: str,
    approval_note: str,
    module: Optional[str] = None,
    evidence: Optional[list] = None,
) -> str:
    """Build formatted HTML confirmation message."""
    clean_summary = summary.replace("\n", " ").strip()
    extra_info = ""
    if module and module != "General":
        extra_info += f"<br/>📦 <b>Module</b>: {module}"
    if evidence:
        extra_info += f"<br/>🔍 <b>Evidence</b>: {'; '.join(str(e) for e in evidence[:2])}"

    return (
        f"🎟️ <b>Jira Ticket Created</b>: <a href='{ticket_url}'><b>{ticket_key}</b></a><br/>"
        f"📅 <b>Date & Time</b>: {created_at}<br/>"
        f"📝 <b>Summary</b>: {clean_summary}<br/>"
        f"⚡ <b>Type & Priority</b>: {issue_type} | {priority}{extra_info}<br/>"
        f"👤 <b>Assignee</b>: {assignee}<br/>"
        f"🗣️ <b>Reporter</b>: {reporter}<br/>"
        f"✅ <i>{approval_note}</i>"
    )


async def send_ticket_created_notification(
    ticket_key: str,
    ticket_url: str,
    summary: str,
    issue_type: str = "Task",
    priority: str = "Medium",
    assignee: str = "Unassigned",
    reporter: str = "Teams User",
    approval_note: str = "Approved by PM via Teams 👍",
    created_at: Optional[str] = None,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    module: Optional[str] = None,
    evidence: Optional[list] = None,
) -> Dict[str, Any]:
    """Send Jira ticket confirmation to Teams via Workflow Webhook (Option A) or Graph API."""
    timestamp = created_at or get_current_timestamp_str()
    webhook_url = (config.teams.webhook_url or "").strip()

    # Step 1: Option A - Post via Teams Workflow Webhook
    if webhook_url:
        logger.info(
            f"Posting Jira creation notification to Teams Webhook for {ticket_key}",
            extra={"event": "TEAMS_WEBHOOK_POST", "ticket": ticket_key},
        )
        card_payload = build_adaptive_card_payload(
            ticket_key=ticket_key,
            ticket_url=ticket_url,
            summary=summary,
            issue_type=issue_type,
            priority=priority,
            created_at=timestamp,
            assignee=assignee,
            reporter=reporter,
            approval_note=approval_note,
            module=module,
            evidence=evidence,
        )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(webhook_url, json=card_payload)
                if res.status_code in (200, 201, 202):
                    logger.info(
                        f"✅ Successfully posted ticket notification to Teams group chat for {ticket_key}",
                        extra={"event": "TEAMS_WEBHOOK_SUCCESS", "status": res.status_code},
                    )
                    return {"success": True, "method": "webhook", "status": res.status_code}
                else:
                    logger.warning(
                        f"Teams Webhook returned {res.status_code}: {res.text[:200]}",
                        extra={"event": "TEAMS_WEBHOOK_WARNING", "status": res.status_code},
                    )
        except Exception as webhook_err:
            logger.error(
                f"Error calling Teams Webhook URL: {webhook_err}",
                extra={"event": "TEAMS_WEBHOOK_ERROR", "error": str(webhook_err)},
            )

    # Step 2: Fallback to Graph API (if chat_id or channel_id available)
    html_msg = build_html_message(
        ticket_key=ticket_key,
        ticket_url=ticket_url,
        summary=summary,
        issue_type=issue_type,
        priority=priority,
        created_at=timestamp,
        assignee=assignee,
        reporter=reporter,
        approval_note=approval_note,
        module=module,
        evidence=evidence,
    )

    effective_chat_id = chat_id or config.teams.chat_id
    effective_team_id = team_id or config.teams.team_id
    effective_channel_id = channel_id or config.teams.channel_id

    if effective_chat_id:
        from src.graph_client import send_chat_message
        graph_res = await send_chat_message(effective_chat_id, html_msg)
        return {"success": bool(graph_res), "method": "graph_chat", "result": graph_res}
    elif effective_team_id and effective_channel_id and parent_message_id:
        from src.graph_client import send_channel_reply
        graph_res = await send_channel_reply(effective_team_id, effective_channel_id, parent_message_id, html_msg)
        return {"success": bool(graph_res), "method": "graph_channel", "result": graph_res}

    return {"success": False, "method": "none", "error": "No Teams Webhook URL or Graph destination available"}
