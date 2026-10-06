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


def get_app_base_url() -> str:
    """Return public tunnel URL if configured, otherwise local server URL."""
    if config.webhook_public_url:
        return config.webhook_public_url.rstrip("/")
    try:
        from src.services.subscription_manager import get_active_subscription_info
        info = get_active_subscription_info()
        tun = info.get("tunnelUrl") or info.get("notificationUrl")
        if tun:
            import urllib.parse
            parsed = urllib.parse.urlparse(tun)
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return f"http://localhost:{config.port}"


def build_pending_approval_card(
    message_id: str,
    issues: list,
    reporter: str,
    raw_message: str,
    created_at: str,
    base_url: Optional[str] = None,
    duplicate_warning: Optional[Dict[str, Any]] = None,
    extractor_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Adaptive Card prompting PM for final confirmation (Approve / Decline) of identified issue(s)."""
    app_base = base_url or get_app_base_url()
    issue_count = len(issues)
    clean_raw = (raw_message or "").replace("\n", " ").strip()
    if len(clean_raw) > 120:
        clean_raw = clean_raw[:117] + "..."

    issue_summaries = "\n".join(f"• Issue #{i+1}: {iss.get('summary', 'Issue')}" for i, iss in enumerate(issues))
    dup_text = f"\n⚠️ Potential duplicate of {duplicate_warning.get('key')} created recently\n" if duplicate_warning else ""
    plain_text = (
        f"📋 PM Triage: {issue_count} Issue(s) Identified\n"
        f"Reporter: {reporter}\n"
        f"{dup_text}"
        f"Message: {clean_raw}\n\n"
        f"Issues:\n{issue_summaries}\n\n"
        f"👉 To Approve: React with 🎟️ or 🎫\n"
        f"👉 To Reject: React with ❌"
    )

    facts = [
        {"title": "Reporter:", "value": reporter},
        {"title": "Identified Issues:", "value": f"{issue_count} issue{'s' if issue_count > 1 else ''}"},
    ]
    if extractor_mode:
        facts.append({"title": "Engine:", "value": extractor_mode})
    facts.append({"title": "Original Text:", "value": clean_raw or "[No text, see attachments]"})

    body_elements = [
        {
            "type": "Container",
            "style": "warning",
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"📋 Issue Triage — PM Approval Required ({issue_count} Issue{'s' if issue_count > 1 else ''} Identified)",
                    "weight": "Bolder",
                    "size": "Medium",
                    "color": "Warning",
                    "wrap": True,
                },
                {
                    "type": "TextBlock",
                    "text": "Review identified issue specifications below. React to Approve or Reject:",
                    "isSubtle": True,
                    "wrap": True,
                },
            ],
        },
    ]

    if duplicate_warning:
        dup_key = duplicate_warning.get("key", "Recent Ticket")
        dup_url = duplicate_warning.get("url", "#")
        dup_conf = int(duplicate_warning.get("confidence", 0.5) * 100)
        body_elements.append({
            "type": "Container",
            "style": "attention",
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"⚠️ **Potential Duplicate**: Similar ticket [{dup_key}]({dup_url}) was created recently ({dup_conf}% keyword match).",
                    "weight": "Bolder",
                    "color": "Attention",
                    "wrap": True,
                }
            ],
        })

    body_elements.append({
        "type": "FactSet",
        "facts": facts,
    })

    for i, iss in enumerate(issues):
        iss_summary = iss.get("summary", "Issue Report")
        iss_type = iss.get("issue_type", "Bug")
        iss_priority = iss.get("priority", "Medium")
        iss_module = iss.get("affected_module", "General")
        iss_assignee = iss.get("suggested_assignee") or "Unassigned"
        iss_obs = iss.get("observed_behavior") or ""
        if len(iss_obs) > 140:
            iss_obs = iss_obs[:137] + "..."

        issue_facts = [
            {"title": "Type & Priority:", "value": f"{iss_type} | {iss_priority}"},
            {"title": "Module:", "value": iss_module},
            {"title": "Specialist:", "value": iss_assignee},
        ]

        issue_items = [
            {
                "type": "TextBlock",
                "text": f"Issue #{i+1}: {iss_summary}",
                "weight": "Bolder",
                "color": "Accent",
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": issue_facts,
            },
        ]
        if iss_obs:
            issue_items.append({
                "type": "TextBlock",
                "text": f"Details: {iss_obs}",
                "isSubtle": True,
                "wrap": True,
            })

        body_elements.append({
            "type": "Container",
            "style": "emphasis",
            "items": issue_items,
        })

    body_elements.append({
        "type": "Container",
        "style": "emphasis",
        "items": [
            {
                "type": "TextBlock",
                "text": "Click **Approve** / **Reject** below, or simply react 👍 to Approve (100% inside Teams, zero redirects):",
                "weight": "Bolder",
                "size": "Medium",
                "color": "Accent",
                "wrap": True,
            },
        ],
    })

    confirm_label = (
        f"✅ Approve All ({issue_count})"
        if issue_count > 1
        else "✅ Approve"
    )
    reject_label = (
        f"❌ Reject All ({issue_count})"
        if issue_count > 1
        else "❌ Reject"
    )
    actions = [
        {
            "type": "Action.OpenUrl",
            "title": confirm_label,
            "url": f"{app_base}/api/jira/confirm-approval/{message_id}",
        },
        {
            "type": "Action.OpenUrl",
            "title": reject_label,
            "url": f"{app_base}/api/jira/decline-approval/{message_id}",
        },
    ]

    if issue_count > 1:
        for i, iss in enumerate(issues[:3]):
            actions.append({
                "type": "Action.OpenUrl",
                "title": f"Approve #{i+1}",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/{i}",
            })
            actions.append({
                "type": "Action.OpenUrl",
                "title": f"Reject #{i+1}",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/{i}",
            })

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
                    "body": body_elements,
                    "actions": actions,
                },
            }
        ],
    }


async def send_pending_approval_notification(
    message_id: str,
    issues: list,
    reporter: str = "Client",
    raw_message: str = "",
    created_at: Optional[str] = None,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    parent_message_id: Optional[str] = None,
    duplicate_warning: Optional[Dict[str, Any]] = None,
    extractor_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Send confirmation request card to Teams asking PM to confirm or decline ticket creation."""
    timestamp = created_at or get_current_timestamp_str()
    webhook_url = (config.teams.webhook_url or "").strip()

    card_payload = build_pending_approval_card(
        message_id=message_id,
        issues=issues,
        reporter=reporter,
        raw_message=raw_message,
        created_at=timestamp,
        duplicate_warning=duplicate_warning,
        extractor_mode=extractor_mode,
    )

    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(webhook_url, json=card_payload)
                if res.status_code in (200, 201, 202):
                    logger.info(
                        f"✅ Successfully posted pending approval card to Teams for message {message_id}",
                        extra={"event": "TEAMS_PENDING_APPROVAL_POST", "status": res.status_code},
                    )
                    return {"success": True, "method": "webhook", "status": res.status_code}
        except Exception as webhook_err:
            logger.error(f"Error calling Teams Webhook for pending approval: {webhook_err}")

    # Fallback to Graph API
    effective_chat_id = chat_id or config.teams.chat_id
    if effective_chat_id:
        from src.graph_client import send_chat_message
        html_msg = f"<b>📋 PM Triage: {len(issues)} Issue(s) Identified</b><br/>Reporter: {reporter}<br/>React 🎟️ to Approve or ❌ to Decline."
        graph_res = await send_chat_message(effective_chat_id, html_msg)
        return {"success": bool(graph_res), "method": "graph_chat", "result": graph_res}

    return {"success": False, "method": "none"}


def build_declined_card_payload(
    message_id: str,
    issues: list,
    reporter: str,
    approver: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Adaptive Card confirming PM decline / cancellation."""
    issue_titles = [iss.get("summary", "Issue") for iss in issues] if issues else ["Issue Report"]
    plain_text = f"❌ Ticket Creation Rejected by {approver}\nIssues: {', '.join(issue_titles)}\nNo Jira tickets were created."

    facts = [
        {"title": "Rejected By:", "value": approver},
        {"title": "Reporter:", "value": reporter},
        {"title": "Status:", "value": "Rejected / Not Created in Jira"},
    ]
    if reason:
        facts.append({"title": "Reason:", "value": reason})

    issue_items = [{"type": "TextBlock", "text": f"• {title}", "wrap": True} for title in issue_titles]

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
                            "style": "attention",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "❌ Ticket Creation Rejected by PM",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Attention",
                                    "wrap": True,
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"{approver} rejected creating Jira ticket(s) for this message:",
                                    "isSubtle": True,
                                    "wrap": True,
                                },
                            ],
                        },
                        {
                            "type": "Container",
                            "items": issue_items,
                        },
                        {
                            "type": "FactSet",
                            "facts": facts,
                        },
                    ],
                },
            }
        ],
    }


async def send_ticket_declined_notification(
    message_id: str,
    issues: list,
    reporter: str = "Client",
    approver: Optional[str] = None,
    reason: Optional[str] = None,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send card to Teams confirming ticket decline/cancellation."""
    if not approver:
        from src.services.member_sync_service import get_active_pm_from_excel
        pm_info = get_active_pm_from_excel()
        approver = f"PM {pm_info.get('name', 'Project Manager')}"

    webhook_url = (config.teams.webhook_url or "").strip()
    card_payload = build_declined_card_payload(
        message_id=message_id,
        issues=issues,
        reporter=reporter,
        approver=approver,
        reason=reason,
    )

    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(webhook_url, json=card_payload)
                if res.status_code in (200, 201, 202):
                    return {"success": True, "method": "webhook", "status": res.status_code}
        except Exception as webhook_err:
            logger.error(f"Error calling Teams Webhook for declined notification: {webhook_err}")

    effective_chat_id = chat_id or config.teams.chat_id
    if effective_chat_id:
        from src.graph_client import send_chat_message
        html_msg = f"❌ <b>Ticket Creation Declined by {approver}</b><br/>No Jira tickets were created."
        graph_res = await send_chat_message(effective_chat_id, html_msg)
        return {"success": bool(graph_res), "method": "graph_chat", "result": graph_res}

    return {"success": False, "method": "none"}


def build_pm_followup_reminder_card(
    message_id: str,
    pm_name: str,
    pm_user_id: Optional[str],
    reporter: str,
    elapsed_minutes: int,
    issues: list,
    raw_message: str,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a compact one-line Adaptive Card mentioning PM for follow-up on client issue."""
    app_base = base_url or get_app_base_url()

    first_summary = ""
    if issues and len(issues) > 0:
        first_summary = issues[0].get("summary", "")
    clean_snippet = first_summary or (raw_message or "").replace("\n", " ").strip()
    if len(clean_snippet) > 90:
        clean_snippet = clean_snippet[:87] + "..."

    # One-line concise message mentioning PM directly
    card_text = (
        f"⏰ <at>{pm_name}</at> Please review client issue from **{reporter}**: "
        f"*\"{clean_snippet}\"* — react 🎟️ to approve or ❌ to decline."
    )
    plain_text = f"⏰ @{pm_name} Please review client issue from {reporter}: \"{clean_snippet}\" (React 🎟️ to approve, ❌ to decline)"

    body_elements: list = [
        {
            "type": "TextBlock",
            "text": card_text,
            "wrap": True,
            "size": "Medium",
        }
    ]

    actions: list = [
        {
            "type": "Action.OpenUrl",
            "title": "🎟️ Approve",
            "url": f"{app_base}/api/jira/confirm-issue/{message_id}",
        },
        {
            "type": "Action.OpenUrl",
            "title": "❌ Decline",
            "url": f"{app_base}/api/jira/decline-issue/{message_id}",
        },
    ]

    card_content: Dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body_elements,
        "actions": actions,
    }

    # Tag PM using official Teams mention schema
    if pm_name:
        card_content["msteams"] = {
            "entities": [
                {
                    "type": "mention",
                    "text": f"<at>{pm_name}</at>",
                    "mentioned": {
                        "id": pm_user_id or "",
                        "name": pm_name,
                    },
                }
            ]
        }

    return {
        "type": "message",
        "text": plain_text,
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card_content,
            }
        ],
    }


async def send_pm_followup_reminder(
    message_id: str,
    pm_name: str,
    pm_user_id: Optional[str],
    reporter: str,
    elapsed_minutes: int,
    issues: list,
    raw_message: str,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send Teams follow-up card with PM mention in reply mode to client's message if possible, falling back to webhook."""
    webhook_url = (config.teams.webhook_url or "").strip()
    effective_chat_id = chat_id or config.teams.chat_id
    effective_team_id = team_id or config.teams.team_id
    effective_channel_id = channel_id or config.teams.channel_id

    first_summary = ""
    if issues and len(issues) > 0:
        first_summary = issues[0].get("summary", "")
    snippet = first_summary or (raw_message or "").replace("\n", " ").strip()
    if len(snippet) > 90:
        snippet = snippet[:87] + "..."

    # HTML content for Graph API with standard @mention tag
    html_msg = (
        f"⏰ <at id=\"0\">{pm_name}</at> Please review client issue from <b>{reporter}</b>: "
        f"<i>\"{snippet}\"</i> (React 🎟️ to approve, ❌ to decline)"
    )
    mentions_payload = [
        {
            "id": 0,
            "mentionText": pm_name,
            "mentioned": {
                "user": {
                    "id": pm_user_id or "",
                    "displayName": pm_name,
                }
            },
        }
    ] if pm_name else None

    # Step 1: Attempt direct reply mode via Microsoft Graph API
    # 1A. Teams Channel Reply (threaded reply under client message)
    if effective_team_id and effective_channel_id and message_id:
        try:
            from src.graph_client import send_channel_reply
            reply_res = await send_channel_reply(
                team_id=effective_team_id,
                channel_id=effective_channel_id,
                parent_message_id=message_id,
                content=html_msg,
                mentions=mentions_payload,
            )
            if reply_res:
                logger.info(
                    f"⏰ Successfully posted PM follow-up as channel reply to message {message_id}",
                    extra={"event": "TEAMS_PM_REMINDER_SUCCESS", "method": "channel_reply", "messageId": message_id},
                )
                return {"success": True, "method": "channel_reply", "result": reply_res}
        except Exception as c_err:
            logger.debug(f"Graph channel reply error: {c_err}")

    # 1B. Teams Chat Quoted Reply (replyWithQuote in group chat / 1:1 chat)
    if effective_chat_id and message_id:
        try:
            from src.graph_client import send_chat_reply_with_quote
            quote_res = await send_chat_reply_with_quote(
                chat_id=effective_chat_id,
                message_id=message_id,
                content=html_msg,
                mentions=mentions_payload,
            )
            if quote_res:
                logger.info(
                    f"⏰ Successfully posted PM follow-up as chat quoted reply for message {message_id}",
                    extra={"event": "TEAMS_PM_REMINDER_SUCCESS", "method": "chat_quote_reply", "messageId": message_id},
                )
                return {"success": True, "method": "chat_quote_reply", "result": quote_res}
        except Exception as q_err:
            logger.debug(f"Graph chat quoted reply error: {q_err}")

    # Step 2: Post compact one-line card via Teams Webhook
    if webhook_url:
        card_payload = build_pm_followup_reminder_card(
            message_id=message_id,
            pm_name=pm_name,
            pm_user_id=pm_user_id,
            reporter=reporter,
            elapsed_minutes=elapsed_minutes,
            issues=issues,
            raw_message=raw_message,
        )
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(webhook_url, json=card_payload)
                if res.status_code in (200, 201, 202):
                    logger.info(
                        f"⏰ Successfully posted compact PM follow-up reminder card to Teams for message {message_id}",
                        extra={"event": "TEAMS_PM_REMINDER_SUCCESS", "method": "webhook", "messageId": message_id},
                    )
                    return {"success": True, "method": "webhook", "status": res.status_code}
                else:
                    logger.warning(
                        f"Teams Webhook reminder returned HTTP {res.status_code}: {res.text[:200]}",
                        extra={"event": "TEAMS_PM_REMINDER_WARN", "status": res.status_code},
                    )
        except Exception as webhook_err:
            logger.error(f"Error calling Teams Webhook for reminder: {webhook_err}")

    # Step 3: Fallback to Graph API chat message
    if effective_chat_id:
        try:
            from src.graph_client import send_chat_message
            graph_res = await send_chat_message(effective_chat_id, html_msg, mentions=mentions_payload)
            if graph_res:
                return {"success": True, "method": "graph_chat", "result": graph_res}
        except Exception as g_err:
            logger.debug(f"Graph chat reminder error: {g_err}")

    return {"success": False, "method": "none", "error": "No delivery channel succeeded"}


