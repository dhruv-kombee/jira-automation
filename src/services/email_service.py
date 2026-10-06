"""Outlook & SMTP Email Notification Service for PM Follow-ups.

Constructs and delivers rich, responsive HTML alert emails to Project Managers (PMs)
when a client issue has not received a reaction or approval within the SLA window (15 minutes).
Supports Microsoft Graph API (/sendMail) and standard SMTP (Outlook / Office 365).
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
import httpx

from src.config import config
from src.logger import logger


def build_pm_followup_email_html(
    pm_name: str,
    reporter_name: str,
    elapsed_minutes: int,
    issues: List[Dict[str, Any]],
    raw_message: str,
    message_id: str,
    created_at_str: str,
    teams_web_url: Optional[str] = None,
    dashboard_url: Optional[str] = None,
) -> str:
    """Build a professional, modern, responsive HTML email for PM follow-up alerts."""
    base_dash = (dashboard_url or config.webhook_public_url or f"http://localhost:{config.port}").rstrip("/")
    clean_raw = (raw_message or "").strip().replace("<", "&lt;").replace(">", "&gt;")

    issues_html = ""
    for idx, iss in enumerate(issues):
        s_title = iss.get("summary", "Client Reported Issue")
        s_type = iss.get("issue_type", "Task")
        s_priority = iss.get("priority", "Medium")
        s_module = iss.get("affected_module", "General")
        s_assignee = iss.get("suggested_assignee", "Unassigned")
        s_obs = iss.get("observed_behavior") or iss.get("description") or ""

        # Priority color tag
        p_color = "#dc2626" if s_priority in ("Highest", "High") else "#f59e0b" if s_priority == "Medium" else "#10b981"

        issues_html += f"""
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #2563eb; border-radius: 6px; padding: 16px; margin-bottom: 16px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b;">Issue #{idx+1}</span>
                <span style="background-color: {p_color}15; color: {p_color}; border: 1px solid {p_color}40; padding: 2px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600;">{s_priority} Priority</span>
            </div>
            <h3 style="margin: 0 0 10px 0; font-size: 16px; font-weight: 700; color: #0f172a;">{s_title}</h3>
            
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 13px;">
                <tr>
                    <td style="padding: 4px 0; color: #64748b; width: 30%;"><strong>Type:</strong></td>
                    <td style="padding: 4px 0; color: #1e293b;">{s_type}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 0; color: #64748b;"><strong>Module:</strong></td>
                    <td style="padding: 4px 0; color: #1e293b;">{s_module}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 0; color: #64748b;"><strong>Specialist:</strong></td>
                    <td style="padding: 4px 0; color: #1e293b;">{s_assignee}</td>
                </tr>
            </table>

            {f'<div style="background: #ffffff; border: 1px solid #e2e8f0; padding: 10px 12px; border-radius: 4px; font-size: 12px; color: #475569; margin-bottom: 12px; line-height: 1.5;"><strong>Observed:</strong> {s_obs}</div>' if s_obs else ''}

            <div style="display: flex; gap: 8px; margin-top: 10px;">
                <a href="{base_dash}/api/jira/confirm-issue/{message_id}/{idx}" style="background-color: #16a34a; color: #ffffff; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 12px; font-weight: 600; display: inline-block;">🎟️ Approve Issue #{idx+1}</a>
                <a href="{base_dash}/api/jira/decline-issue/{message_id}/{idx}" style="background-color: #ef4444; color: #ffffff; text-decoration: none; padding: 8px 14px; border-radius: 5px; font-size: 12px; font-weight: 600; display: inline-block; margin-left: 8px;">❌ Decline Issue #{idx+1}</a>
            </div>
        </div>
        """

    teams_btn = f"""
    <a href="{teams_web_url}" style="background-color: #4f46e5; color: #ffffff; text-decoration: none; padding: 10px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; display: inline-block; margin-right: 10px;">💬 Open in Microsoft Teams</a>
    """ if teams_web_url else ""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Action Required: Client Issue Pending PM Review</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px 12px; color: #1e293b; line-height: 1.6;">
    <div style="max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -2px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
        
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 24px 28px; border-bottom: 3px solid #3b82f6;">
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span style="font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.1em; color: #94a3b8;">Jira Automation Hub</span>
                <span style="background-color: #ef4444; color: #ffffff; padding: 3px 10px; border-radius: 9999px; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;">⏰ {elapsed_minutes}m SLA WARNING</span>
            </div>
            <h1 style="color: #ffffff; margin: 12px 0 4px 0; font-size: 20px; font-weight: 700;">Action Required: Client Issue Pending Review</h1>
            <p style="color: #cbd5e1; margin: 0; font-size: 13px;">Automated SLA escalation sent to Project Manager</p>
        </div>

        <!-- Alert Ribbon -->
        <div style="background-color: #fef2f2; border-bottom: 1px solid #fee2e2; padding: 14px 28px;">
            <p style="margin: 0; font-size: 13px; color: #991b1b; font-weight: 500;">
                ⚠️ <strong>Hello {pm_name}:</strong> A client issue reported by <strong>{reporter_name}</strong> has received <strong>no reaction or confirmation</strong> for over {elapsed_minutes} minutes. Please review and react 🎟️ in Teams or approve below.
            </p>
        </div>

        <!-- Body Content -->
        <div style="padding: 24px 28px;">
            
            <!-- Metadata Cards -->
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px 16px; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                    <tr>
                        <td style="padding: 3px 0; color: #64748b; width: 35%;"><strong>Client Reporter:</strong></td>
                        <td style="padding: 3px 0; color: #0f172a; font-weight: 600;">{reporter_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 3px 0; color: #64748b;"><strong>Received At:</strong></td>
                        <td style="padding: 3px 0; color: #0f172a;">{created_at_str}</td>
                    </tr>
                    <tr>
                        <td style="padding: 3px 0; color: #64748b;"><strong>Elapsed Idle Time:</strong></td>
                        <td style="padding: 3px 0; color: #dc2626; font-weight: 700;">{elapsed_minutes} minutes (No PM Reaction)</td>
                    </tr>
                    <tr>
                        <td style="padding: 3px 0; color: #64748b;"><strong>Target Jira Project:</strong></td>
                        <td style="padding: 3px 0; color: #0f172a; font-weight: 600;">{config.jira.project_key} ({config.jira.base_url})</td>
                    </tr>
                </table>
            </div>

            <!-- Original Message Preview -->
            <div style="margin-bottom: 20px;">
                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 6px;">Original Client Message:</div>
                <div style="background-color: #f1f5f9; border-left: 3px solid #64748b; padding: 10px 14px; border-radius: 4px; font-size: 13px; color: #334155; font-style: italic; white-space: pre-wrap;">"{clean_raw}"</div>
            </div>

            <!-- Extracted Issues Section -->
            <div style="margin-bottom: 20px;">
                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 10px;">Identified Tickets Awaiting Approval ({len(issues)}):</div>
                {issues_html}
            </div>

            <!-- Primary Global Actions -->
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; text-align: center; margin-top: 24px;">
                <div style="font-size: 13px; font-weight: 600; color: #0f172a; margin-bottom: 12px;">Quick PM Actions:</div>
                <div>
                    {teams_btn}
                    <a href="{base_dash}/api/jira/confirm-issue/{message_id}" style="background-color: #16a34a; color: #ffffff; text-decoration: none; padding: 10px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; display: inline-block; margin-right: 10px;">🎟️ Approve All in Jira</a>
                    <a href="{base_dash}/api/jira/decline-issue/{message_id}" style="background-color: #ef4444; color: #ffffff; text-decoration: none; padding: 10px 18px; border-radius: 6px; font-size: 13px; font-weight: 600; display: inline-block;">❌ Decline</a>
                </div>
                <div style="margin-top: 14px;">
                    <a href="{base_dash}" style="color: #2563eb; font-size: 12px; text-decoration: none;">View Live Operations Dashboard &rarr;</a>
                </div>
            </div>

        </div>

        <!-- Footer -->
        <div style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 28px; text-align: center; font-size: 11px; color: #94a3b8;">
            <p style="margin: 0 0 4px 0;">This is an automated notification from <strong>Jira Automation Hub</strong>.</p>
            <p style="margin: 0;">Dispatched because no PM reaction was detected within {config.pm_reminder_timeout_minutes} minutes of client message arrival.</p>
        </div>

    </div>
</body>
</html>
"""


async def send_email_via_graph(
    to_email: str,
    subject: str,
    html_body: str,
    sender_upn: Optional[str] = None,
) -> Dict[str, Any]:
    """Send email using Microsoft Graph API /users/{upn}/sendMail."""
    from src.graph_client import get_access_token

    try:
        token = get_access_token()
    except Exception as auth_err:
        return {"success": False, "error": f"Failed to acquire Microsoft Graph token: {auth_err}"}

    # Decide sender: specific configured sender, or the recipient themselves (as mailbox proxy)
    sender = sender_upn or config.email.graph_sender or to_email
    endpoint = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_email,
                    }
                }
            ],
            "importance": "high",
        },
        "saveToSentItems": "false",
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(endpoint, json=payload, headers=headers)
            if res.status_code in (200, 202):
                logger.info(
                    f"📧 Successfully sent Outlook follow-up email to {to_email} via Microsoft Graph API",
                    extra={"event": "GRAPH_EMAIL_SENT", "to": to_email, "subject": subject},
                )
                return {"success": True, "method": "graph", "status": res.status_code}
            else:
                err_text = res.text
                logger.warning(
                    f"Microsoft Graph /sendMail returned HTTP {res.status_code}: {err_text[:250]}",
                    extra={"event": "GRAPH_EMAIL_FAILED", "status": res.status_code, "body": err_text[:200]},
                )
                return {
                    "success": False,
                    "status_code": res.status_code,
                    "error": f"Graph API {res.status_code}: {err_text[:200]}",
                }
    except Exception as exc:
        logger.error(f"Error calling Microsoft Graph sendMail: {exc}")
        return {"success": False, "error": str(exc)}


def send_email_via_smtp(
    to_email: str,
    subject: str,
    html_body: str,
) -> Dict[str, Any]:
    """Send email using standard SMTP (Outlook / Office 365 or custom server)."""
    if not config.email.is_smtp_configured:
        return {"success": False, "error": "SMTP is not configured in .env"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.email.smtp_from
    msg["To"] = to_email
    msg["X-Priority"] = "1"
    msg["Importance"] = "High"

    # Attach HTML
    part_html = MIMEText(html_body, "html", "utf-8")
    msg.attach(part_html)

    try:
        if config.email.smtp_port == 465:
            server = smtplib.SMTP_SSL(config.email.smtp_host, config.email.smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(config.email.smtp_host, config.email.smtp_port, timeout=12)
            if config.email.smtp_use_tls:
                server.starttls()

        server.login(config.email.smtp_user, config.email.smtp_password)
        server.sendmail(config.email.smtp_from, [to_email], msg.as_string())
        server.quit()

        logger.info(
            f"📧 Successfully sent follow-up email to {to_email} via SMTP ({config.email.smtp_host})",
            extra={"event": "SMTP_EMAIL_SENT", "to": to_email},
        )
        return {"success": True, "method": "smtp"}
    except Exception as exc:
        logger.error(f"SMTP send failed to {to_email}: {exc}", extra={"event": "SMTP_EMAIL_ERROR"})
        return {"success": False, "error": str(exc)}


async def send_pm_followup_email(
    pm_email: str,
    pm_name: str,
    reporter_name: str,
    elapsed_minutes: int,
    issues: List[Dict[str, Any]],
    raw_message: str,
    message_id: str,
    created_at_str: str,
    teams_web_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified email dispatcher for PM follow-up alerts.
    Tries Microsoft Graph API first; if unavailable or denied, tries SMTP fallback.
    """
    config.reload()
    subject = f"[URGENT] ⏰ Follow-up: Client Issue Pending PM Review ({elapsed_minutes}m unanswered)"
    if issues and issues[0].get("summary"):
        subject = f"[URGENT] ⏰ PM Review Needed ({elapsed_minutes}m): {issues[0].get('summary')}"

    html_content = build_pm_followup_email_html(
        pm_name=pm_name,
        reporter_name=reporter_name,
        elapsed_minutes=elapsed_minutes,
        issues=issues,
        raw_message=raw_message,
        message_id=message_id,
        created_at_str=created_at_str,
        teams_web_url=teams_web_url,
    )

    # 1. Try Microsoft Graph API
    graph_res = await send_email_via_graph(
        to_email=pm_email,
        subject=subject,
        html_body=html_content,
    )
    if graph_res.get("success"):
        return graph_res

    # 2. Try SMTP fallback if Graph failed
    if config.email.is_smtp_configured:
        smtp_res = send_email_via_smtp(
            to_email=pm_email,
            subject=subject,
            html_body=html_content,
        )
        if smtp_res.get("success"):
            return smtp_res

    # Return combined status info
    err_msg = graph_res.get("error", "Failed to deliver email")
    if "403" in err_msg or "ErrorAccessDenied" in err_msg:
        logger.warning(
            f"ℹ️ Outlook email could not be sent via Microsoft Graph: The Azure App Registration "
            f"needs 'Mail.Send' Application permission granted in Azure Portal.",
            extra={"event": "MAIL_SEND_PERMISSION_NOTICE"},
        )
    return {
        "success": False,
        "graph_error": graph_res.get("error"),
        "error": err_msg,
    }
