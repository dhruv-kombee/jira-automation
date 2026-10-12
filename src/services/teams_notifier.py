import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List
from src.config import config
from src.logger import logger


def get_current_timestamp_str() -> str:
    """Return formatted local timestamp with timezone/offset."""
    try:
        now = datetime.now().astimezone()
        return now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_card_timestamp(ts: Optional[str] = None) -> str:
    """Format timestamp into a user-friendly local date and time string."""
    if not ts:
        return get_current_timestamp_str()
    # If already formatted with space, colon, and AM/PM
    if " " in ts and ":" in ts and ("AM" in ts or "PM" in ts):
        return ts
    try:
        clean_ts = str(ts).strip()
        if clean_ts.endswith("Z"):
            clean_ts = clean_ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean_ts)
        local_dt = dt.astimezone() if dt.tzinfo else dt
        return local_dt.strftime("%Y-%m-%d %I:%M:%S %p %Z").strip()
    except Exception:
        return str(ts) or get_current_timestamp_str()


def build_adaptive_card_payload(
    ticket_key: str,
    ticket_url: str,
    summary: str,
    issue_type: str = "Task",
    priority: str = "Medium",
    created_at: Optional[str] = None,
    assignee: str = "Unassigned",
    reporter: Optional[str] = None,
    approval_note: Optional[str] = None,
    module: Optional[str] = None,
    evidence: Optional[list] = None,
    status: str = "To Do",
) -> Dict[str, Any]:
    """Build a minimal, compact Teams Adaptive Card payload sent after reaction/approval.
    Strictly contains: Topic Details, Scrum Link, Assignee, and Status.
    """
    clean_summary = summary.replace("\n", " ").strip()
    scrum_link_md = f"[{ticket_key}]({ticket_url})" if ticket_url else ticket_key

    plain_text = (
        f"🎟️ Ticket Created: {ticket_key}\n"
        f"Topic Details: {clean_summary}\n"
        f"Scrum Link: {ticket_url or ticket_key}\n"
        f"Assignee: {assignee}\n"
        f"Status: {status or 'To Do'}"
    )

    facts = [
        {"title": "Topic Details:", "value": clean_summary},
        {"title": "Scrum Link:", "value": scrum_link_md},
        {"title": "Assignee:", "value": assignee or "Unassigned"},
        {"title": "Status:", "value": status or "To Do"},
    ]

    display_timestamp = format_card_timestamp(created_at)

    card_content: Dict[str, Any] = {
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
                        "text": "🎟️ Ticket Created",
                        "weight": "Bolder",
                        "size": "Medium",
                        "color": "Good",
                        "wrap": True,
                    },
                    {
                        "type": "TextBlock",
                        "text": display_timestamp,
                        "size": "Small",
                        "spacing": "None",
                        "isSubtle": True,
                        "wrap": True,
                    },
                ],
            },
            {
                "type": "FactSet",
                "facts": facts,
            },
        ],
    }

    if ticket_url:
        card_content["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": f"Open {ticket_key}",
                "url": ticket_url,
            }
        ]

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


def build_html_message(
    ticket_key: str,
    ticket_url: str,
    summary: str,
    issue_type: str = "Task",
    priority: str = "Medium",
    created_at: Optional[str] = None,
    assignee: str = "Unassigned",
    reporter: Optional[str] = None,
    approval_note: Optional[str] = None,
    module: Optional[str] = None,
    evidence: Optional[list] = None,
    status: str = "To Do",
) -> str:
    """Build formatted HTML confirmation message with topic details, scrum link, assignee, and status."""
    clean_summary = summary.replace("\n", " ").strip()
    scrum_link = f"<a href='{ticket_url}'><b>{ticket_key}</b></a>" if ticket_url else f"<b>{ticket_key}</b>"
    return (
        f"🎟️ <b>Ticket Created</b><br/>"
        f"📌 <b>Topic Details</b>: {clean_summary}<br/>"
        f"🔗 <b>Scrum Link</b>: {scrum_link}<br/>"
        f"👤 <b>Assignee</b>: {assignee or 'Unassigned'}<br/>"
        f"📋 <b>Status</b>: {status or 'To Do'}"
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
    status: str = "To Do",
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
            status=status,
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
        status=status,
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
    target_users: Optional[List[Dict[str, Any]]] = None,
    target_role: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal, compact Adaptive Card prompting TL/PM for approval of identified issue(s).
    Strictly focuses on: Topic Details, Scrum Project, and Assignee.
    Per FR-04 and AC-05, all active members of the targeted tier are tagged at once.
    """
    app_base = base_url or get_app_base_url()
    issue_count = len(issues)
    project_key = config.jira.project_key or "SCRUM"

    # Build multi-user mention entities (FR-04 / AC-05)
    mention_entities: List[Dict[str, Any]] = []
    mention_tags: List[str] = []
    if target_users:
        for u in target_users:
            u_name = u.get("display_name") or u.get("name") or "Reviewer"
            u_id = u.get("user_id") or u.get("email") or ""
            mention_entities.append({
                "type": "mention",
                "text": f"<at>{u_name}</at>",
                "mentioned": {
                    "id": u_id,
                    "name": u_name,
                },
            })
            mention_tags.append(f"<at>{u_name}</at>")

    tag_str = " ".join(mention_tags)
    display_timestamp = format_card_timestamp(created_at)
    role_prefix = f"[{target_role}] " if target_role else ""
    header_text = f"{role_prefix}Ticket Confirmation ({issue_count} Issues)" if issue_count > 1 else f"{role_prefix}Ticket Confirmation"
    body_elements: List[Dict[str, Any]] = [
        {
            "type": "Container",
            "style": "warning",
            "items": [
                {
                    "type": "TextBlock",
                    "text": header_text,
                    "weight": "Bolder",
                    "size": "Medium",
                    "color": "Warning",
                    "wrap": True,
                },
                {
                    "type": "TextBlock",
                    "text": display_timestamp,
                    "size": "Small",
                    "spacing": "None",
                    "isSubtle": True,
                    "wrap": True,
                },
            ],
        },
    ]

    if tag_str:
        body_elements.append({
            "type": "TextBlock",
            "text": f"🔔 {tag_str} Please review and confirm ticket creation using the buttons below:",
            "weight": "Bolder",
            "size": "Small",
            "wrap": True,
        })

    if duplicate_warning:
        dup_key = duplicate_warning.get("key", "Recent Ticket")
        dup_url = duplicate_warning.get("url", "#")
        body_elements.append({
            "type": "TextBlock",
            "text": f"⚠️ Potential duplicate of [{dup_key}]({dup_url})",
            "color": "Attention",
            "weight": "Bolder",
            "wrap": True,
        })

    import urllib.parse
    import re
    from src.services.member_sync_service import get_all_members_from_excel

    # Dynamically retrieve team members from Member.xlsx for assignee options (for multi-issue)
    try:
        raw_members = get_all_members_from_excel()
    except Exception:
        raw_members = []

    assignable_choices = []
    seen_names = set()
    for m in raw_members:
        role = (m.get("role") or "").upper()
        if role != "CLIENT":
            c_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip()
            if c_name and c_name not in seen_names:
                seen_names.add(c_name)
                spec = m.get("specialty") or role
                assignable_choices.append({
                    "name": c_name,
                    "specialty": spec,
                    "role": role,
                })

    if not assignable_choices:
        assignable_choices = [
            {"name": "Lead Developer", "specialty": "Backend / Core API", "role": "DEVELOPER"},
            {"name": "Frontend Developer", "specialty": "Frontend & UI", "role": "DEVELOPER"},
            {"name": "Project Manager", "specialty": "Sprint & Delivery Oversight", "role": "PM"},
        ]

    facts: List[Dict[str, str]] = []
    plain_summary_lines = []

    for i, iss in enumerate(issues):
        iss_summary = (iss.get("summary") or "Issue Report").replace("\n", " ").strip()
        if issue_count > 1:
            facts.append({"title": f"Topic Details #{i+1}:", "value": iss_summary})
            plain_summary_lines.append(f"• Issue #{i+1}: {iss_summary}")
        else:
            facts.append({"title": "Topic Details:", "value": iss_summary})
            plain_summary_lines.append(f"• Topic: {iss_summary}")

    facts.append({"title": "Project:", "value": project_key})
    facts.append({"title": "Reporter Name:", "value": reporter or "Client"})
    plain_summary_lines.append(f"• Project: {project_key}")
    plain_summary_lines.append(f"• Reporter: {reporter or 'Client'}")

    body_elements.append({
        "type": "FactSet",
        "facts": facts,
    })

    if issue_count == 1:
        iss1 = issues[0]
        dev1 = (iss1.get("suggested_assignee") or "Unassigned").strip()
        dev1_short = dev1.split()[0] if dev1 != "Unassigned" else "Dev"
        summary1 = (iss1.get("summary") or "Issue Report").replace("\n", " ").strip()
        if len(summary1) > 40:
            summary1 = summary1[:37] + "..."

        issue1_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve ({dev1_short})",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}?assignee={urllib.parse.quote_plus(dev1)}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown (Web)",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            issue1_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}?assignee={urllib.parse.quote_plus(m_name)}",
            })

        actions = [
            {
                "type": "Action.ShowCard",
                "title": f"Approve ({dev1_short}) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Approve Issue: {summary1}",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": issue1_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject",
                "url": f"{app_base}/api/jira/decline-approval/{message_id}",
            },
        ]
    elif issue_count == 2:
        # Issue #1:
        iss1 = issues[0]
        dev1 = (iss1.get("suggested_assignee") or "Unassigned").strip()
        dev1_short = dev1.split()[0] if dev1 != "Unassigned" else "Dev 1"
        summary1 = (iss1.get("summary") or "Issue #1").replace("\n", " ").strip()
        if len(summary1) > 40:
            summary1 = summary1[:37] + "..."

        issue1_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve ({dev1_short})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0?assignee={urllib.parse.quote_plus(dev1)}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown (Web)",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            issue1_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0?assignee={urllib.parse.quote_plus(m_name)}",
            })
        issue1_subactions.append({
            "type": "Action.OpenUrl",
            "title": "❌ Reject Issue #1 Only",
            "url": f"{app_base}/api/jira/decline-issue/{message_id}/0",
        })

        # Issue #2:
        iss2 = issues[1]
        dev2 = (iss2.get("suggested_assignee") or "Unassigned").strip()
        dev2_short = dev2.split()[0] if dev2 != "Unassigned" else "Dev 2"
        summary2 = (iss2.get("summary") or "Issue #2").replace("\n", " ").strip()
        if len(summary2) > 40:
            summary2 = summary2[:37] + "..."

        issue2_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve ({dev2_short})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1?assignee={urllib.parse.quote_plus(dev2)}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown (Web)",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            issue2_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1?assignee={urllib.parse.quote_plus(m_name)}",
            })
        issue2_subactions.append({
            "type": "Action.OpenUrl",
            "title": "❌ Reject Issue #2 Only",
            "url": f"{app_base}/api/jira/decline-issue/{message_id}/1",
        })

        # Bulk All Issues:
        approve_all_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": "⚡ Approve All (Suggested Assignees)",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown for All (Web)",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            approve_all_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign All to {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-approval/{message_id}?assignee={urllib.parse.quote_plus(m_name)}",
            })

        actions = [
            {
                "type": "Action.ShowCard",
                "title": f"Approve #1 ({dev1_short}) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Approve Issue #1: {summary1}",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": issue1_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject #1",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/0",
            },
            {
                "type": "Action.ShowCard",
                "title": f"Approve #2 ({dev2_short}) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Approve Issue #2: {summary2}",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": issue2_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject #2",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/1",
            },
            {
                "type": "Action.ShowCard",
                "title": "✅ Approve All (2) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "Approve Both Issues #1 & #2:",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": approve_all_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject All (2)",
                "url": f"{app_base}/api/jira/decline-approval/{message_id}",
            },
        ]
    else:
        # Multi-issue card (> 2 issues):
        iss1 = issues[0]
        dev1 = (iss1.get("suggested_assignee") or "Unassigned").strip()
        dev1_short = dev1.split()[0] if dev1 != "Unassigned" else "Dev 1"
        summary1 = (iss1.get("summary") or "Issue #1").replace("\n", " ").strip()
        if len(summary1) > 40:
            summary1 = summary1[:37] + "..."

        issue1_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve ({dev1_short})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0?assignee={urllib.parse.quote_plus(dev1)}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown (Web)",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            issue1_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/0?assignee={urllib.parse.quote_plus(m_name)}",
            })

        iss2 = issues[1]
        dev2 = (iss2.get("suggested_assignee") or "Unassigned").strip()
        dev2_short = dev2.split()[0] if dev2 != "Unassigned" else "Dev 2"
        summary2 = (iss2.get("summary") or "Issue #2").replace("\n", " ").strip()
        if len(summary2) > 40:
            summary2 = summary2[:37] + "..."

        issue2_subactions = [
            {
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve ({dev2_short})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1?assignee={urllib.parse.quote_plus(dev2)}",
            },
            {
                "type": "Action.OpenUrl",
                "title": "🌐 Assignee Dropdown (Web)",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1",
            },
        ]
        for m in assignable_choices:
            m_name = m["name"]
            short_role = "PM" if m["role"] == "PM" else (m["specialty"].split("&")[0].split("/")[0].strip())
            issue2_subactions.append({
                "type": "Action.OpenUrl",
                "title": f"Assign {m_name} ({short_role})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/1?assignee={urllib.parse.quote_plus(m_name)}",
            })

        more_issue_actions = []
        for i in range(2, len(issues)):
            cur_iss = issues[i]
            cur_dev = (cur_iss.get("suggested_assignee") or "Unassigned").strip()
            cur_short = cur_dev.split()[0] if cur_dev != "Unassigned" else f"Dev {i+1}"
            more_issue_actions.append({
                "type": "Action.OpenUrl",
                "title": f"⚡ Approve #{i+1} ({cur_short})",
                "url": f"{app_base}/api/jira/confirm-issue/{message_id}/{i}?assignee={urllib.parse.quote_plus(cur_dev)}",
            })
            more_issue_actions.append({
                "type": "Action.OpenUrl",
                "title": f"❌ Reject #{i+1}",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/{i}",
            })

        actions = [
            {
                "type": "Action.ShowCard",
                "title": f"Approve #1 ({dev1_short}) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Approve Issue #1: {summary1}",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": issue1_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject #1",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/0",
            },
            {
                "type": "Action.ShowCard",
                "title": f"Approve #2 ({dev2_short}) ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Approve Issue #2: {summary2}",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": issue2_subactions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": "❌ Reject #2",
                "url": f"{app_base}/api/jira/decline-issue/{message_id}/1",
            },
            {
                "type": "Action.ShowCard",
                "title": f"📋 More ({issue_count - 2}) / All ▾",
                "card": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"Remaining Issues ({issue_count - 2} total):",
                            "weight": "Bolder",
                            "size": "Small",
                            "wrap": True,
                        }
                    ],
                    "actions": more_issue_actions,
                },
            },
            {
                "type": "Action.OpenUrl",
                "title": f"❌ Reject All ({issue_count})",
                "url": f"{app_base}/api/jira/decline-approval/{message_id}",
            },
        ]

    plain_tags = " ".join(f"@{u.get('display_name', 'Lead')}" for u in target_users) if target_users else ""
    prefix = f"{plain_tags} " if plain_tags else ""
    plain_text = (
        f"{prefix}Ticket Confirmation ({issue_count} Issue{'s' if issue_count > 1 else ''})\n"
        + "\n".join(plain_summary_lines)
    )

    card_content: Dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body_elements,
        "actions": actions,
    }
    if mention_entities:
        card_content["msteams"] = {
            "entities": mention_entities,
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
    target_users: Optional[List[Dict[str, Any]]] = None,
    target_role: Optional[str] = None,
) -> Dict[str, Any]:
    """Send confirmation request card to Teams asking TL/PM to confirm or decline ticket creation.
    Per FR-04 and AC-05, all active members of the targeted tier are tagged at once.
    """
    timestamp = created_at or get_current_timestamp_str()
    webhook_url = (config.teams.webhook_url or "").strip()

    # Determine targeted tier members if not explicitly provided
    if target_users is None:
        from src.services.member_sync_service import get_active_tls, get_active_pms
        active_tls = get_active_tls()
        if active_tls:
            target_users = active_tls
            target_role = target_role or "TL"
        else:
            active_pms = get_active_pms()
            target_users = active_pms
            target_role = target_role or "PM"

    card_payload = build_pending_approval_card(
        message_id=message_id,
        issues=issues,
        reporter=reporter,
        raw_message=raw_message,
        created_at=timestamp,
        duplicate_warning=duplicate_warning,
        extractor_mode=extractor_mode,
        target_users=target_users,
        target_role=target_role,
    )

    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(webhook_url, json=card_payload)
                if res.status_code in (200, 201, 202):
                    logger.info(
                        f"✅ Successfully posted pending approval card to Teams for message {message_id} (tagged: {len(target_users or [])})",
                        extra={"event": "TEAMS_PENDING_APPROVAL_POST", "status": res.status_code, "targetRole": target_role},
                    )
                    return {"success": True, "method": "webhook", "status": res.status_code}
        except Exception as webhook_err:
            logger.error(f"Error calling Teams Webhook for pending approval: {webhook_err}")

    # Fallback to Graph API with multi-mentions
    effective_chat_id = chat_id or config.teams.chat_id
    if effective_chat_id:
        from src.graph_client import send_chat_message
        graph_mentions = []
        graph_tags = []
        if target_users:
            for idx, u in enumerate(target_users):
                u_name = u.get("display_name") or u.get("name") or "Lead"
                u_id = u.get("user_id") or u.get("email") or ""
                graph_mentions.append({
                    "id": idx,
                    "mentionText": u_name,
                    "mentioned": {
                        "user": {
                            "displayName": u_name,
                            "id": u_id,
                            "userIdentityType": "aadUser",
                        }
                    },
                })
                graph_tags.append(f'<at id="{idx}">{u_name}</at>')
        mention_html = (" ".join(graph_tags) + " ") if graph_tags else ""
        role_label = target_role or "TL/PM"
        html_msg = f"{mention_html}<b>📋 {role_label} Triage: {len(issues)} Issue(s) Identified</b><br/>Reporter: {reporter}<br/>React 👍 to Approve or ❌ to Decline."
        graph_res = await send_chat_message(effective_chat_id, html_msg, mentions=graph_mentions if graph_mentions else None)
        return {"success": bool(graph_res), "method": "graph_chat", "result": graph_res}

    return {"success": False, "method": "none"}


def build_declined_card_payload(
    message_id: str,
    issues: list,
    reporter: str,
    approver: str,
    reason: Optional[str] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Adaptive Card confirming PM decline / cancellation."""
    display_timestamp = format_card_timestamp(created_at)
    issue_titles = [iss.get("summary", "Issue") for iss in issues] if issues else ["Issue Report"]
    clean_approver = approver[3:].strip() if approver.startswith("PM ") and approver[3:].strip() else approver
    plain_text = (
        f"Ticket Creation Rejected ({display_timestamp})\n"
        f"• Rejected By: {clean_approver}\n"
        f"• Reporter: {reporter}\n"
        f"• Issues: {', '.join(issue_titles)}\n"
        f"No Jira tickets were created."
    )

    facts = [
        {"title": "Rejected By:", "value": clean_approver},
        {"title": "Reporter:", "value": reporter},
    ]

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
                                    "text": "Ticket Creation Rejected",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Attention",
                                    "wrap": True,
                                },
                                {
                                    "type": "TextBlock",
                                    "text": display_timestamp,
                                    "size": "Small",
                                    "spacing": "None",
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
    created_at: Optional[str] = None,
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
        created_at=created_at,
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
    mention_users: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a compact one-line Adaptive Card mentioning PM / TL for follow-up on client issue."""
    app_base = base_url or get_app_base_url()

    first_summary = ""
    if issues and len(issues) > 0:
        first_summary = issues[0].get("summary", "")
    clean_snippet = first_summary or (raw_message or "").replace("\n", " ").strip()
    if len(clean_snippet) > 90:
        clean_snippet = clean_snippet[:87] + "..."

    # Build mentions entities and tag string
    mention_entities: List[Dict[str, Any]] = []
    mention_tags: List[str] = []
    if mention_users:
        for u in mention_users:
            u_name = u.get("display_name") or u.get("name") or "Reviewer"
            u_id = u.get("user_id") or u.get("email") or ""
            mention_entities.append({
                "type": "mention",
                "text": f"<at>{u_name}</at>",
                "mentioned": {
                    "id": u_id,
                    "name": u_name,
                },
            })
            mention_tags.append(f"<at>{u_name}</at>")
        tag_str = " ".join(mention_tags)
        plain_tag = " ".join(f"@{u.get('display_name') or u.get('name')}" for u in mention_users)
    elif pm_name:
        mention_entities.append({
            "type": "mention",
            "text": f"<at>{pm_name}</at>",
            "mentioned": {
                "id": pm_user_id or "",
                "name": pm_name,
            },
        })
        tag_str = f"<at>{pm_name}</at>"
        plain_tag = f"@{pm_name}"
    else:
        tag_str = "@Reviewers"
        plain_tag = "@Reviewers"

    # One-line concise message mentioning PM directly
    card_text = (
        f"⏰ {tag_str} Please review client issue from **{reporter}**: "
        f"*\"{clean_snippet}\"* — react 🎟️ to approve or ❌ to decline."
    )
    plain_text = f"⏰ {plain_tag} Please review client issue from {reporter}: \"{clean_snippet}\" (React 🎟️ to approve, ❌ to decline)"

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
    if mention_entities:
        card_content["msteams"] = {
            "entities": mention_entities,
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
    target_role: str = "PM",
    mention_users: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Send Teams follow-up card with mention in reply mode to client's message if possible, falling back to webhook.
    Supports single or multiple @mentions for TLs and PMs.
    """
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

    # Build mentions payload and tag string
    mentions_payload = []
    mention_tags = []
    if mention_users and len(mention_users) > 0:
        for idx, u in enumerate(mention_users):
            u_name = u.get("name") or u.get("display_name") or target_role
            u_id = u.get("user_id") or ""
            mentions_payload.append({
                "id": idx,
                "mentionText": u_name,
                "mentioned": {
                    "user": {
                        "id": u_id,
                        "displayName": u_name,
                    }
                },
            })
            mention_tags.append(f'<at id="{idx}">{u_name}</at>')
        tag_str = " ".join(mention_tags)
    elif pm_name:
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
        ]
        tag_str = f'<at id="0">{pm_name}</at>'
    else:
        tag_str = f"@{target_role}"
        mentions_payload = None

    stage_prefix = "⏰ [10m TL Review]" if target_role == "TL" else ("🚨 [20m PM Escalation]" if target_role == "PM" else "⏰ Follow-up:")
    html_msg = (
        f"{stage_prefix} {tag_str} Please review client issue from <b>{reporter}</b>: "
        f"<i>\"{snippet}\"</i> (React 🎟️ to approve, ❌ to decline)"
    )

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
            mention_users=mention_users,
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


