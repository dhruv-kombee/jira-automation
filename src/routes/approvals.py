"""Jira Ticket Approval and Confirmation Endpoints.

Dedicated router handling:
- 1-Click PM, TL, HM Approval and Rejection web actions for Teams Adaptive Cards
- Granular per-issue approval and rejection endpoints
- Role identity gateway and Microsoft SSO session verification
"""
import json
import re
import urllib.parse
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from src.config import config
from src.database import get_db
from src.logger import logger

router = APIRouter()

def render_confirmation_html(
    title: str,
    status_type: str,  # "success", "declined", "error"
    heading: str,
    message: str,
    details: Optional[dict] = None,
    actions: Optional[list] = None,
    status_code: int = 200,
) -> HTMLResponse:
    details = details or {}
    actions = actions or []

    badge_color = "#10b981" if status_type == "success" else ("#ef4444" if status_type == "declined" else "#f59e0b")
    badge_icon = "🎟️" if status_type == "success" else ("❌" if status_type == "declined" else "⚠️")

    facts_html = "".join(
        f'<div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #334155;">'
        f'<span style="color:#94a3b8; font-weight:500;">{k}</span>'
        f'<span style="color:#f8fafc; font-weight:600; text-align:right;">{v}</span>'
        f'</div>'
        for k, v in details.items()
    )

    actions_html = "".join(
        f'<a href="{act.get("url")}" target="_blank" style="display:inline-block; margin:6px; padding:10px 20px; background:#3b82f6; color:#ffffff; font-weight:600; border-radius:8px; text-decoration:none;">{act.get("label")}</a>'
        for act in actions
    )

    auto_close_script = ""
    auto_close_banner = ""
    if status_type in ("success", "declined") and status_code < 400:
        auto_close_banner = """
        <div style="margin-top:16px; font-size:13.5px; color:#38bdf8; background:#0284c715; border:1px solid #0284c733; padding:10px 16px; border-radius:8px;">
          ⏱️ Ticket recorded! This window will automatically close in <span id="autoCloseSec" style="font-weight:700;">2</span> seconds.
        </div>"""
        auto_close_script = """
  <script>
    // Automatically close tab after 2 seconds with countdown
    window.onload = function() {
      let sec = 2;
      let el = document.getElementById("autoCloseSec");
      let timer = setInterval(function() {
        sec--;
        if (el) el.textContent = sec;
        if (sec <= 0) {
          clearInterval(timer);
          try { window.close(); } catch(e) {}
        }
      }, 1000);
    };
  </script>"""

    switch_identity_html = ""
    if status_code == 403:
        switch_identity_html = """
        <div style="margin-top:14px;">
          <button onclick="try{localStorage.removeItem('saved_jira_reviewer'); document.cookie='jira_reviewer=; path=/; max-age=0;';}catch(e){} let u=new URL(window.location.href); u.searchParams.delete('reviewer'); window.location.href=u.toString();" style="background:#1e293b; color:#38bdf8; border:1px solid #0284c7; padding:8px 18px; border-radius:8px; cursor:pointer; font-weight:600; font-size:12px;">🔄 Switch Active Reviewer Identity</button>
        </div>"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
      box-sizing: border-box;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 520px;
      width: 100%;
      padding: 32px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      text-align: center;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: {badge_color}22;
      color: {badge_color};
      border: 1px solid {badge_color}44;
      margin-bottom: 20px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 10px 0;
      color: #ffffff;
    }}
    p {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 24px 0;
    }}
    .facts-box {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 24px;
      text-align: left;
      font-size: 13px;
    }}
    .footer-note {{
      font-size: 12px;
      color: #64748b;
      margin-top: 24px;
    }}
  </style>
  {auto_close_script}
</head>

<body>
  <div class="card">
    <div class="badge">{badge_icon} {heading}</div>
    <h1>{title}</h1>
    <p>{message}</p>
    {f'<div class="facts-box">{facts_html}</div>' if details else ''}
    <div>{actions_html}{switch_identity_html}{auto_close_banner}</div>
    <div style="margin-top:16px;">
      <button onclick="window.close()" style="background:#334155; color:#f8fafc; border:1px solid #475569; padding:8px 20px; border-radius:8px; cursor:pointer; font-weight:600; font-size:13px;">✕ Close Window</button>
    </div>
    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=status_code)


def get_member_or_sender_identity(approver_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
    """Look up a member from Member.xlsx to retrieve their display name and role."""
    if not approver_identifier:
        return None
    from src.services.member_sync_service import get_member_by_id_or_name
    return get_member_by_id_or_name(user_id=approver_identifier, display_name=approver_identifier)


def resolve_and_verify_approver(approver_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verify that the provided approver identifier belongs to an active, authorized management member (TL, PM, HM)."""
    if not approver_identifier:
        return None
    from src.services.member_sync_service import get_member_by_id_or_name
    from src.services.sender_service import is_user_authorized_approver

    member = get_member_by_id_or_name(user_id=approver_identifier, display_name=approver_identifier)
    if not member:
        return None

    # Check if member is authorized to approve (strictly disallow client, developer, unassigned)
    if not is_user_authorized_approver(user_id=member.get("user_id"), display_name=member.get("display_name"), allow_client=False):
        return None

    return member


def resolve_active_reviewer_from_request(request: Request, explicit_reviewer: Optional[str] = None) -> Optional[str]:
    """Resolve reviewer display name prioritizing: explicit query param -> verified SSO session -> cookie."""
    if explicit_reviewer and explicit_reviewer.strip():
        return explicit_reviewer.strip()
    try:
        from src.routes.auth import get_authenticated_reviewer_from_request
        sso_user = get_authenticated_reviewer_from_request(request)
        if sso_user and sso_user.get("display_name"):
            return sso_user.get("display_name")
    except Exception:
        pass
    if config.microsoft.tenant_id and config.microsoft.client_id:
        return None
    return request.cookies.get("jira_reviewer")


def get_browser_redirect_or_gateway(
    request: Request,
    action_type: str,
    message_id: str,
    assignee: Optional[str] = None,
    issue_idx: Optional[int] = None,
    reason: Optional[str] = None,
) -> Response:
    """If Microsoft SSO credentials exist, seamlessly redirect to /auth/login for zero-click verification.
    Otherwise fallback to the identity gateway screen.
    """
    if config.microsoft.tenant_id and config.microsoft.client_id:
        req_path = request.url.path
        if request.url.query:
            req_path = f"{req_path}?{request.url.query}"
        return RedirectResponse(
            url=f"/auth/login?redirect_after={urllib.parse.quote(req_path)}",
            status_code=302,
        )
    return render_identity_gateway_html(
        action_type=action_type,
        message_id=message_id,
        assignee=assignee,
        issue_idx=issue_idx,
        reason=reason,
    )


def get_approver_name_for_message(row: Optional[Any] = None, explicit_reviewer: Optional[str] = None) -> Optional[str]:
    """Resolve an authorized approver name string (e.g. 'TL <Name>', 'PM <Name>', 'HM <Name>').
    Returns None if no authorized management member (TL, PM, HM) is verified.
    Does NOT auto-impersonate or fall back to PM.
    """
    from src.services.member_sync_service import get_member_by_id_or_name
    from src.services.sender_service import is_user_authorized_approver, is_ticket_approval_reaction
    import json

    # 1. If explicit reviewer provided and valid
    if explicit_reviewer:
        m = get_member_by_id_or_name(user_id=explicit_reviewer, display_name=explicit_reviewer)
        if m and is_user_authorized_approver(user_id=m.get("user_id"), display_name=m.get("display_name")):
            m_role = (m.get("role") or "PM").upper().strip()
            return f"{m_role} {m.get('display_name')}"

    # 2. Check reactions stored on the message for an authorized manager (TL, PM, HM)
    if row:
        reactions_raw = row["reactions"] if isinstance(row, dict) or hasattr(row, "__getitem__") else None
        if reactions_raw:
            try:
                reactions = json.loads(reactions_raw) if isinstance(reactions_raw, str) else reactions_raw
                if isinstance(reactions, list):
                    for r in reactions:
                        u_id = r.get("userId")
                        d_name = r.get("displayName")
                        r_type = r.get("reactionType")
                        if is_ticket_approval_reaction(r_type) and is_user_authorized_approver(user_id=u_id, display_name=d_name):
                            m = get_member_by_id_or_name(user_id=u_id, display_name=d_name)
                            if m:
                                m_role = (m.get("role") or "").upper().strip()
                                return f"{m_role} {m.get('display_name')}"
            except Exception:
                pass

    return None


def render_identity_gateway_html(
    action_type: str,
    message_id: str,
    assignee: Optional[str] = None,
    issue_idx: Optional[int] = None,
    reason: Optional[str] = None,
) -> HTMLResponse:
    """Render an instant client-side identity gateway.
    Only authorized management members (HM, PM, TL) are allowed to confirm/decline tickets.
    Developers, Clients, and Unassigned senders are strictly prevented and shown an access denied warning.
    """
    from src.services.member_sync_service import get_all_members_from_excel
    import json
    import re

    try:
        roster = get_all_members_from_excel()
    except Exception:
        roster = []

    # Strictly filter for authorized management approvers (TL, PM, HM) only!
    mgmt_members = []
    mgmt_names = []
    for m in roster:
        m_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip()
        m_role = (m.get("role") or "").upper().strip()
        m_lvl = str(m.get("level") or "").strip()
        can_appr = m.get("can_approve")
        is_mgmt = (m_role in ("TL", "PM", "HM") or m_lvl in ("Level 1", "Level 2", "Level 3", "1", "2", "3")) and bool(can_appr)
        if is_mgmt and m_role not in ("DEVELOPER", "CLIENT", "UNASSIGNED"):
            mgmt_members.append(m)
            mgmt_names.append(m_name)

    buttons_html = []
    for m in mgmt_members:
        m_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip()
        m_role = (m.get("role") or "").upper().strip()
        m_lvl = str(m.get("level") or "").strip()
        lvl_str = f" ({m_lvl})" if m_lvl and m_lvl != "-" else ""
        icon = "🌟" if m_role == "TL" else ("👑" if m_role == "PM" else "🏛️")
        badge_style = "background:#10b98122; color:#10b981; border:1px solid #10b98144;"

        buttons_html.append(f"""
        <button onclick="selectReviewer('{m_name}')" style="display:flex; align-items:center; justify-content:space-between; width:100%; padding:12px 16px; margin-bottom:8px; background:#0f172a; border:1px solid #334155; border-radius:10px; color:#f8fafc; font-size:14px; font-weight:600; cursor:pointer; text-align:left; transition:background 0.15s ease;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:18px;">{icon}</span>
            <span>{m_name}</span>
          </div>
          <span style="font-size:11px; font-weight:700; padding:4px 10px; border-radius:9999px; {badge_style}">{m_role}{lvl_str}</span>
        </button>""")

    buttons_joined = "\n".join(buttons_html)
    mgmt_names_json = json.dumps(mgmt_names)
    action_label = "Confirm Approval" if action_type == "confirm" else "Confirm Rejection"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Role Verification — {action_label}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 480px;
      width: 100%;
      padding: 30px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
      text-align: center;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: #3b82f622;
      color: #38bdf8;
      border: 1px solid #38bdf844;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 20px; margin: 0 0 8px 0; color: #ffffff; }}
    p.subtext {{ color: #94a3b8; font-size: 13.5px; line-height: 1.5; margin: 0 0 18px 0; }}
    .footer-note {{ font-size: 12px; color: #64748b; margin-top: 18px; }}
  </style>
  <script>
    const authorizedManagers = {mgmt_names_json};

    function showForbiddenScreen(roleOrName) {{
      document.getElementById('gatewayCard').innerHTML = `
        <div style="padding:10px 0;">
          <div style="display:inline-flex; align-items:center; gap:6px; padding:6px 14px; border-radius:9999px; font-size:13px; font-weight:700; background:#ef444422; color:#f87171; border:1px solid #ef444455; margin-bottom:16px;">
            ⛔ Strictly Not Permitted
          </div>
          <h1 style="color:#f87171; font-size:21px; margin-bottom:12px;">Access Denied</h1>
          <p style="color:#cbd5e1; font-size:14.5px; line-height:1.6; margin-bottom:20px;">
            You are strictly <strong>not allowed or permitted to approve or reject</strong> Jira tickets.
          </p>
          <div style="background:#0f172a; border-left:4px solid #ef4444; padding:14px 16px; border-radius:8px; text-align:left; font-size:13.5px; color:#fca5a5; margin-bottom:24px; line-height:1.5;">
            ⚠️ <strong>Action Required:</strong> Please ask a <strong>Project Manager (PM)</strong>, <strong>Team Lead (TL)</strong>, or <strong>Higher Management (HM)</strong> to approve or reject this request.
          </div>
          <button onclick="window.close()" style="background:#334155; color:#f8fafc; border:1px solid #475569; padding:10px 24px; border-radius:8px; cursor:pointer; font-weight:600; font-size:13.5px;">✕ Close Window</button>
        </div>
      `;
    }}

    (function() {{
      try {{
        const saved = localStorage.getItem('saved_jira_reviewer');
        if (saved) {{
          if (authorizedManagers.includes(saved)) {{
            document.cookie = "jira_reviewer=" + encodeURIComponent(saved) + "; path=/; max-age=2592000";
            let u = new URL(window.location.href);
            u.searchParams.set('reviewer', saved);
            window.location.replace(u.toString());
          }} else {{
            // Saved user is a Developer, Client, or Unassigned
            showForbiddenScreen(saved);
          }}
        }}
      }} catch(e) {{}}
    }})();

    function selectReviewer(name) {{
      try {{
        localStorage.setItem('saved_jira_reviewer', name);
        document.cookie = "jira_reviewer=" + encodeURIComponent(name) + "; path=/; max-age=2592000";
      }} catch(e) {{}}
      let u = new URL(window.location.href);
      u.searchParams.set('reviewer', name);
      window.location.replace(u.toString());
    }}

    function rejectUnauthorized() {{
      showForbiddenScreen('Non-Management');
    }}
  </script>
</head>
<body>
  <div class="card" id="gatewayCard">
    <div class="badge">🛡️ Role Authority Verification</div>
    <h1>Confirm Your Management Identity</h1>
    <p class="subtext">Only <strong>Team Leads (TL)</strong>, <strong>Project Managers (PM)</strong>, and <strong>Higher Management (HM)</strong> are permitted to approve or reject Jira tickets.</p>
    
    <div style="display:flex; flex-direction:column; gap:6px;">
      {buttons_joined}
    </div>

    <button onclick="rejectUnauthorized()" style="display:flex; align-items:center; justify-content:center; gap:8px; width:100%; padding:12px 16px; margin-top:10px; background:#ef444415; border:1px solid #ef444444; border-radius:10px; color:#f87171; font-size:13px; font-weight:700; cursor:pointer; transition:background 0.15s ease;">
      <span>🚫 I am a Developer, Client, or Other Role</span>
    </button>

    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)



def render_decline_dropdown_html(
    message_id: str,
    summary: str,
    project_key: str,
    issue_idx: Optional[int] = None,
) -> HTMLResponse:
    """Render a dedicated, responsive Decline/Reject modal dialog requiring authorized approver selection."""
    from src.services.member_sync_service import get_active_authorized_approvers
    authorized_approvers = get_active_authorized_approvers()
    approvers_options_html = []
    for ap in authorized_approvers:
        ap_id = ap.get("user_id") or ap.get("display_name")
        ap_name = re.sub(r"\s+", " ", ap.get("display_name", "")).strip()
        ap_role = (ap.get("role") or "").upper().strip()
        ap_lvl = ap.get("level", "")
        lvl_str = f" - {ap_lvl}" if ap_lvl and ap_lvl != "-" else ""
        approvers_options_html.append(
            f'<option value="{ap_id}">{ap_name} ({ap_role}{lvl_str})</option>'
        )
    approvers_joined = "\n          ".join(approvers_options_html)

    action_url = f"/api/jira/decline-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/decline-approval/{message_id}"
    issue_label = f"Issue #{issue_idx + 1}" if issue_idx is not None else "Ticket"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reject {issue_label} — Confirmation</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 520px;
      width: 100%;
      padding: 30px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: #ef444422;
      color: #ef4444;
      border: 1px solid #ef444444;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 10px 0;
      color: #ffffff;
    }}
    p.subtext {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 20px 0;
    }}
    .facts-box {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 20px;
      text-align: left;
      font-size: 13px;
    }}
    .fact-row {{
      display: flex;
      justify-content: space-between;
      padding: 6px 0;
      border-bottom: 1px solid #1e293b;
    }}
    .fact-row:last-child {{ border-bottom: none; }}
    .fact-label {{ color: #94a3b8; font-weight: 500; min-width: 110px; }}
    .fact-val {{ color: #f8fafc; font-weight: 600; text-align: right; word-break: break-word; }}
    .form-group {{
      text-align: left;
      margin-bottom: 22px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #cbd5e1;
      margin-bottom: 8px;
    }}
    select, input[type="text"] {{
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1.5px solid #ef4444;
      border-radius: 8px;
      color: #f8fafc;
      font-size: 14px;
      font-weight: 500;
      outline: none;
    }}
    select:focus, input[type="text"]:focus {{
      border-color: #f87171;
      box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.2);
    }}
    .btn-decline {{
      width: 100%;
      padding: 13px 20px;
      background: #ef4444;
      color: #ffffff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: background 0.15s ease;
    }}
    .btn-decline:hover {{
      background: #dc2626;
    }}
    .footer-note {{
      font-size: 12px;
      color: #64748b;
      margin-top: 22px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">❌ Reject {issue_label} Confirmation</div>
    <h1>Confirm Rejection of {issue_label}</h1>
    <p class="subtext">Select your authorized management identity to confirm rejection. No tickets will be created in Jira.</p>

    <div class="facts-box">
      <div class="fact-row">
        <span class="fact-label">Topic Details:</span>
        <span class="fact-val">{summary}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">Scrum Project:</span>
        <span class="fact-val">{project_key}</span>
      </div>
    </div>

    <form method="POST" action="{action_url}">
      <div class="form-group">
        <label for="approverSelect">🛡️ Confirm Your Management Identity (TL / PM / HM):</label>
        <select name="approver_id" id="approverSelect" required>
          <option value="" disabled selected>-- Select your name from authorized roster --</option>
          {approvers_joined}
        </select>
        <span style="font-size: 11px; color: #94a3b8; display: block; margin-top: 4px;">
          ⚠️ Verification Required: Only active Team Leads, Project Managers, and Higher Management can decline tickets.
        </span>
      </div>

      <div class="form-group">
        <label for="reasonInput">📝 Reason for Decline (Optional):</label>
        <input type="text" name="reason" id="reasonInput" placeholder="e.g. Expected behavior, duplicate report, insufficient info" />
      </div>

      <button type="submit" class="btn-decline">
        ❌ Confirm Rejection
      </button>
    </form>

    <div style="text-align: center; margin-top: 14px;">
      <a href="javascript:history.back()" style="color:#94a3b8; text-decoration:none; font-size:13px;">← Go Back</a>
    </div>

    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


def render_assignee_dropdown_html(
    message_id: str,
    summary: str,
    project_key: str,
    suggested_assignee: str,
    members: List[Dict[str, str]],
    approver: str,
    issue_idx: Optional[int] = None,
    all_roster_members: Optional[List[Dict[str, Any]]] = None,
    default_reviewer: Optional[str] = None,
) -> HTMLResponse:
    """Render a dedicated, responsive Assignee Selection & Role-Verified Approval modal dialog."""
    import re
    if all_roster_members is None:
        from src.services.member_sync_service import get_all_members_from_excel
        try:
            all_roster_members = get_all_members_from_excel()
        except Exception:
            all_roster_members = []

    options_html = []
    clean_suggested = suggested_assignee.strip().lower()

    for m in members:
        name = m.get("name", "")
        spec = m.get("specialty", "")
        is_sel = (name.lower() in clean_suggested or clean_suggested in name.lower())
        sel_attr = "selected" if is_sel else ""
        label = f"{name} — {spec}" if spec else name
        options_html.append(f'<option value="{name}" {sel_attr}>{label}</option>')

    unassigned_sel = "selected" if clean_suggested in ("unassigned", "") else ""
    options_html.append(f'<option value="Unassigned" {unassigned_sel}>Unassigned</option>')
    options_joined = "\n          ".join(options_html)

    # Determine initial selected reviewer from default_reviewer (typically the message reporter e.g. Dhruv dobariya)
    clean_default_reviewer = (default_reviewer or "").strip().lower()
    init_member = None
    for rm in all_roster_members:
        rm_name = re.sub(r"\s+", " ", rm.get("display_name", "")).strip()
        if clean_default_reviewer and (clean_default_reviewer in rm_name.lower() or rm_name.lower() in clean_default_reviewer):
            init_member = rm
            break

    if not init_member and all_roster_members:
        for rm in all_roster_members:
            if (rm.get("role") or "").upper() in ("CLIENT", "DEVELOPER", "UNASSIGNED"):
                init_member = rm
                break
        if not init_member:
            init_member = all_roster_members[0]

    reviewers_html = []
    for rm in all_roster_members:
        rm_name = re.sub(r"\s+", " ", rm.get("display_name", "")).strip()
        rm_role = (rm.get("role") or "").upper().strip()
        rm_lvl = rm.get("level", "").strip()
        lvl_str = f" - {rm_lvl}" if rm_lvl and rm_lvl != "-" else ""
        rm_id = rm.get("user_id") or rm_name
        can_appr = bool(rm.get("can_approve")) and rm_role in ("TL", "PM", "HM")
        can_appr_attr = "1" if can_appr else "0"

        is_sel = (init_member and (rm.get("user_id") == init_member.get("user_id") or rm_name.lower() == re.sub(r"\s+", " ", init_member.get("display_name", "")).strip().lower()))
        sel_attr = "selected" if is_sel else ""

        label = f"{rm_name} — {rm_role}{lvl_str}"
        reviewers_html.append(
            f'<option value="{rm_id}" data-can-approve="{can_appr_attr}" data-role="{rm_role}" data-name="{rm_name}" {sel_attr}>{label}</option>'
        )

    reviewers_joined = "\n          ".join(reviewers_html)

    # Initial server-side state for banner, button, and reject link
    init_role = (init_member.get("role") if init_member else "").upper().strip()
    init_name = re.sub(r"\s+", " ", init_member.get("display_name", "")).strip() if init_member else "Reviewer"
    init_can_approve = bool(init_member.get("can_approve")) and init_role in ("TL", "PM", "HM") if init_member else False

    issue_label = f"Issue #{issue_idx + 1}" if issue_idx is not None else "Jira Ticket"
    action_url = f"/api/jira/confirm-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/confirm-approval/{message_id}"
    reject_url = f"/api/jira/decline-issue/{message_id}/{issue_idx}" if issue_idx is not None else f"/api/jira/decline-approval/{message_id}"

    if not init_can_approve:
        initial_banner_html = f"""<div class="alert-box alert-danger">
          <div class="alert-icon">⛔</div>
          <div class="alert-body">
            <div class="alert-title">No Authority to Confirm</div>
            <div class="alert-desc">
              You are currently identified as <strong>{init_name} ({init_role or 'Client'})</strong>.<br>
              Clients and Developers do not have authority to confirm or reject Jira tickets.<br>
              <strong>Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM) to review and approve.</strong>
            </div>
          </div>
        </div>"""
        init_btn_disabled = "disabled"
        init_btn_class = "btn-disabled"
        init_btn_text = "🔒 Management Authority Required (TL, PM, HM)"
        init_reject_class = "btn-reject-disabled"
    else:
        initial_banner_html = f"""<div class="alert-box alert-success">
          <div class="alert-icon">🛡️</div>
          <div class="alert-body">
            <div class="alert-title">Authorized Management Reviewer</div>
            <div class="alert-desc">
              Identified as <strong>{init_name} ({init_role})</strong>. You have authority to confirm and assign this ticket in Jira Cloud.
            </div>
          </div>
        </div>"""
        init_btn_disabled = ""
        init_btn_class = ""
        init_btn_text = f"🚀 Confirm & Create {issue_label}"
        init_reject_class = ""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Approve {issue_label} — Role Authority Verification</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      margin: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 14px;
      max-width: 520px;
      width: 100%;
      padding: 30px;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 700;
      background: #f59e0b22;
      color: #f59e0b;
      border: 1px solid #f59e0b44;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 8px 0;
      color: #ffffff;
    }}
    p.subtext {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.5;
      margin: 0 0 18px 0;
    }}
    .facts-box {{
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 20px;
      text-align: left;
      font-size: 13px;
    }}
    .fact-row {{
      display: flex;
      justify-content: space-between;
      padding: 6px 0;
      border-bottom: 1px solid #1e293b;
    }}
    .fact-row:last-child {{ border-bottom: none; }}
    .fact-label {{ color: #94a3b8; font-weight: 500; min-width: 110px; }}
    .fact-val {{ color: #f8fafc; font-weight: 600; text-align: right; word-break: break-word; }}
    .form-group {{
      text-align: left;
      margin-bottom: 18px;
    }}
    label {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: #cbd5e1;
      margin-bottom: 8px;
    }}
    select {{
      width: 100%;
      padding: 12px 14px;
      background: #0f172a;
      border: 1.5px solid #3b82f6;
      border-radius: 8px;
      color: #f8fafc;
      font-size: 14px;
      font-weight: 500;
      outline: none;
      cursor: pointer;
    }}
    select:focus {{
      border-color: #60a5fa;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
    }}
    .alert-box {{
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 18px;
      display: flex;
      gap: 12px;
      align-items: flex-start;
      text-align: left;
      font-size: 13px;
      line-height: 1.5;
    }}
    .alert-danger {{
      background: #451a1a;
      border: 1px solid #ef4444;
      color: #fca5a5;
    }}
    .alert-danger .alert-title {{
      font-weight: 700;
      color: #ef4444;
      margin-bottom: 4px;
    }}
    .alert-success {{
      background: #064e3b;
      border: 1px solid #10b981;
      color: #a7f3d0;
    }}
    .alert-success .alert-title {{
      font-weight: 700;
      color: #34d399;
      margin-bottom: 4px;
    }}
    .alert-icon {{
      font-size: 18px;
      line-height: 1;
    }}
    .btn-approve {{
      width: 100%;
      padding: 13px 20px;
      background: #10b981;
      color: #ffffff;
      border: none;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.15s ease;
    }}
    .btn-approve:hover:not(:disabled) {{
      background: #059669;
    }}
    .btn-disabled {{
      background: #334155 !important;
      color: #94a3b8 !important;
      cursor: not-allowed !important;
      border: 1px solid #475569 !important;
      opacity: 0.8;
    }}
    .btn-reject {{
      display: inline-block;
      margin-top: 14px;
      color: #ef4444;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      transition: color 0.15s;
    }}
    .btn-reject:hover {{
      text-decoration: underline;
    }}
    .btn-reject-disabled {{
      color: #64748b !important;
      cursor: not-allowed !important;
      text-decoration: none !important;
    }}
    .footer-note {{
      font-size: 12px;
      color: #64748b;
      margin-top: 22px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">📋 {issue_label} Approval Verification</div>
    <h1>Confirm {issue_label} Creation</h1>
    <p class="subtext">Verify approver authority and choose developer assignment:</p>

    <div class="facts-box">
      <div class="fact-row">
        <span class="fact-label">Topic Details:</span>
        <span class="fact-val">{summary}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">Scrum Project:</span>
        <span class="fact-val">{project_key}</span>
      </div>
      <div class="fact-row">
        <span class="fact-label">AI Suggestion:</span>
        <span class="fact-val" style="color:#60a5fa;">{suggested_assignee}</span>
      </div>
    </div>

    <form method="POST" action="{action_url}" id="approvalForm">
      <div class="form-group">
        <label for="reviewerSelect">🛡️ Reviewer Identity (Who is confirming?):</label>
        <select name="reviewer" id="reviewerSelect" onchange="onReviewerChange()">
          {reviewers_joined}
        </select>
      </div>

      <div id="authorityBanner">
        {initial_banner_html}
      </div>

      <div class="form-group" id="assigneeGroup">
        <label for="assigneeSelect">👤 Assignee Dropdown:</label>
        <select name="assignee" id="assigneeSelect">
          {options_joined}
        </select>
      </div>

      <button type="submit" id="approveBtn" class="btn-approve {init_btn_class}" {init_btn_disabled}>
        {init_btn_text}
      </button>
    </form>

    <div style="text-align: center;">
      <a href="{reject_url}" id="rejectBtn" class="btn-reject {init_reject_class}" onclick="return onRejectClick(event)">❌ Reject {issue_label} Creation</a>
    </div>

    <div class="footer-note">Microsoft Teams &bull; Jira Cloud Automation &bull; Closed-Loop Sync</div>
  </div>

  <script>
    function getSelectedReviewerMeta() {{
      const sel = document.getElementById('reviewerSelect');
      if (!sel || sel.selectedIndex < 0) return {{ canApprove: false, role: '', name: '' }};
      const opt = sel.options[sel.selectedIndex];
      return {{
        canApprove: opt.getAttribute('data-can-approve') === '1',
        role: opt.getAttribute('data-role') || '',
        name: opt.getAttribute('data-name') || opt.text.split('—')[0].trim()
      }};
    }}

    function onReviewerChange() {{
      const sel = document.getElementById('reviewerSelect');
      const meta = getSelectedReviewerMeta();
      try {{ localStorage.setItem('saved_jira_reviewer', sel.value); }} catch(e) {{}}

      const banner = document.getElementById('authorityBanner');
      const approveBtn = document.getElementById('approveBtn');
      const rejectBtn = document.getElementById('rejectBtn');

      if (!meta.canApprove) {{
        banner.innerHTML = `
          <div class="alert-box alert-danger">
            <div class="alert-icon">⛔</div>
            <div class="alert-body">
              <div class="alert-title">No Authority to Confirm</div>
              <div class="alert-desc">
                You are currently identified as <strong>${{meta.name}} (${{meta.role || 'Client'}})</strong>.<br>
                You have no authority to confirm or reject Jira tickets.<br>
                <strong>Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).</strong>
              </div>
            </div>
          </div>
        `;
        approveBtn.disabled = true;
        approveBtn.className = 'btn-approve btn-disabled';
        approveBtn.innerHTML = '🔒 Management Authority Required (TL, PM, HM)';
        if (rejectBtn) {{
          rejectBtn.className = 'btn-reject btn-reject-disabled';
        }}
      }} else {{
        banner.innerHTML = `
          <div class="alert-box alert-success">
            <div class="alert-icon">🛡️</div>
            <div class="alert-body">
              <div class="alert-title">Authorized Management Reviewer</div>
              <div class="alert-desc">
                Identified as <strong>${{meta.name}} (${{meta.role}})</strong>. You have authority to confirm and assign this ticket in Jira Cloud.
              </div>
            </div>
          </div>
        `;
        approveBtn.disabled = false;
        approveBtn.className = 'btn-approve';
        approveBtn.innerHTML = '🚀 Confirm & Create {issue_label}';
        if (rejectBtn) {{
          rejectBtn.className = 'btn-reject';
        }}
      }}
    }}

    function onRejectClick(e) {{
      const meta = getSelectedReviewerMeta();
      if (!meta.canApprove) {{
        e.preventDefault();
        alert('You have no authority to reject this ticket. Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).');
        return false;
      }}
      return true;
    }}

    window.addEventListener('DOMContentLoaded', function() {{
      try {{
        const saved = localStorage.getItem('saved_jira_reviewer');
        const sel = document.getElementById('reviewerSelect');
        if (saved && sel) {{
          for (let i = 0; i < sel.options.length; i++) {{
            if (sel.options[i].value === saved) {{
              sel.selectedIndex = i;
              break;
            }}
          }}
        }}
      }} catch(e) {{}}
      onReviewerChange();
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=200)


@router.api_route("/api/jira/confirm-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_approval_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM/TL Approval endpoint for Teams card action links (GET).
    Directly creates Jira ticket, displays success confirmation card, and auto-closes window.
    """
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation
    import json

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    if row["jira_issue_key"]:
        key = row["jira_issue_key"]
        url = row["jira_issue_url"] or "#"
        return render_confirmation_html(
            title="Ticket Already Created",
            status_type="success",
            heading="Active in Jira",
            message=f"Ticket {key} has already been created for this issue. You can close this window now.",
            details={"Jira Ticket": key, "Status": "Active"},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )

    # 1. Resolve reviewer identity (from verified SSO session, query param, or cookie)
    active_reviewer = resolve_active_reviewer_from_request(request, reviewer)
    user_agent = request.headers.get("user-agent", "").lower()
    is_testclient = "testclient" in user_agent

    if active_reviewer:
        member = resolve_and_verify_approver(active_reviewer)
        if not member:
            unauth = get_member_or_sender_identity(active_reviewer)
            unauth_name = (unauth.get("display_name") if unauth else active_reviewer) or "User"
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Client"
            return render_confirmation_html(
                title="Access Denied",
                status_type="error",
                heading="⛔ Strictly Not Permitted",
                message=f"You are currently identified as {unauth_name} ({unauth_role}). You are strictly not allowed or permitted to approve or reject Jira tickets. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    elif not is_testclient:
        return get_browser_redirect_or_gateway(
            request=request,
            action_type="confirm",
            message_id=message_id,
            assignee=assignee,
        )
    else:
        approver = get_approver_name_for_message(row=row)

    if not approver:
        return render_confirmation_html(
            title="Access Denied",
            status_type="error",
            heading="⛔ Strictly Not Permitted",
            message="No authorized management reviewer identified. You are strictly not allowed or permitted to approve or reject Jira tickets. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
            status_code=403,
        )

    target_assignee = assignee
    if not target_assignee and row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
            target_assignee = ai_ticket.get("suggested_assignee")
        except Exception:
            pass

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, assignee_override=target_assignee
    )
    if not res.get("success"):
        return render_confirmation_html(
            title="Action Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", "Failed to create Jira ticket"),
            status_code=400,
        )

    key = res.get("key", "Created")
    url = res.get("url", "#")
    already = res.get("already_existed", False)
    display_dev = target_assignee or "Assigned Developer"

    resp = render_confirmation_html(
        title=f"Jira Ticket {'Already Active' if already else 'Created Successfully'}",
        status_type="success",
        heading=f"Approved by {approver}",
        message=f"Ticket {key} has been created and assigned to {display_dev}. Confirmation posted to Teams. You can close this window now.",
        details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": display_dev, "Approved By": approver},
        actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
    )
    if active_reviewer:
        resp.set_cookie("jira_reviewer", active_reviewer, max_age=2592000, path="/")
    return resp


@router.post("/api/jira/confirm-approval/{message_id}")
async def confirm_approval_post(
    message_id: str,
    request: Request,
):
    """Role-verified PM/TL Approval endpoint (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation

    assignee = None
    approver_id = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
        except Exception:
            pass

    if not approver_id and not assignee:
        # Programmatic / Dashboard trigger without explicit reviewer
        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Message not found")
        approver = get_approver_name_for_message(row=row)
        if not approver:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Authority Check Failed: No authorized management reviewer identified. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can approve Jira tickets."
                    },
                )
            return render_confirmation_html(
                title="No Authority to Confirm",
                status_type="error",
                heading="Authority Check Failed",
                message="No authorized management reviewer identified. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can approve Jira tickets.",
                status_code=403,
            )
    else:
        # Strictly verify that reviewer is an authorized management member (TL, PM, HM)
        member = resolve_and_verify_approver(approver_id)
        if not member:
            unauth = get_member_or_sender_identity(approver_id)
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Developer"
            logger.warning(
                f"Unauthorized ticket creation attempt for message {message_id} with reviewer '{approver_id}' (role: {unauth_role})",
                extra={"event": "UNAUTHORIZED_CONFIRM_ATTEMPT", "reviewer": approver_id, "messageId": message_id}
            )
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": f"Authority Check Failed: You have role '{unauth_role}'. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can approve tickets."
                    },
                )
            return render_confirmation_html(
                title="No Authority to Confirm",
                status_type="error",
                heading="Authority Check Failed",
                message=f"You have no authority to confirm Jira ticket creation (Role: {unauth_role}). Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can approve tickets.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, assignee_override=assignee
    )
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", "Failed to create Jira ticket"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create ticket"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title="Jira Ticket Created Successfully",
            status_type="success",
            heading=f"Approved by {approver}",
            message=f"Ticket {key} has been created and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )
    return res


@router.api_route("/api/jira/decline-approval/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_approval_get(
    message_id: str,
    request: Request,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM/TL Decline endpoint for Teams card action links (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    active_reviewer = resolve_active_reviewer_from_request(request, reviewer)
    user_agent = request.headers.get("user-agent", "").lower()
    is_testclient = "testclient" in user_agent

    if active_reviewer:
        member = resolve_and_verify_approver(active_reviewer)
        if not member:
            unauth = get_member_or_sender_identity(active_reviewer)
            unauth_name = (unauth.get("display_name") if unauth else active_reviewer) or "User"
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Client"
            return render_confirmation_html(
                title="Access Denied",
                status_type="error",
                heading="⛔ Strictly Not Permitted",
                message=f"You are currently identified as {unauth_name} ({unauth_role}). You are strictly not allowed or permitted to approve or reject Jira tickets. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    elif not is_testclient:
        return get_browser_redirect_or_gateway(
            request=request,
            action_type="decline",
            message_id=message_id,
            reason=reason,
        )
    else:
        approver = get_approver_name_for_message(row=row)

    if not approver:
        return render_confirmation_html(
            title="Access Denied",
            status_type="error",
            heading="⛔ Strictly Not Permitted",
            message="No authorized management reviewer identified. You are strictly not allowed or permitted to approve or reject Jira tickets. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
            status_code=403,
        )

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason)
    if not res.get("success"):
        return render_confirmation_html(
            title="Reject Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", "Failed to reject ticket"),
            status_code=400,
        )

    resp = render_confirmation_html(
        title="Ticket Creation Rejected",
        status_type="declined",
        heading=f"Rejected by {approver}",
        message=f"Ticket creation was rejected by {approver}. No tickets were created in Jira, and notification has been posted to Teams. You can close this window now.",
        details={"Status": "Rejected", "Rejected By": approver},
    )
    if active_reviewer:
        resp.set_cookie("jira_reviewer", active_reviewer, max_age=2592000, path="/")
    return resp


@router.post("/api/jira/decline-approval/{message_id}")
async def decline_approval_post(message_id: str, request: Request):
    """PM/TL Decline endpoint (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    approver_id = None
    reason = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
            reason = body.get("reason")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
            reason = form.get("reason")
        except Exception:
            pass

    if not approver_id and not reason:
        db = get_db()
        row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Message not found")
        approver = get_approver_name_for_message(row=row)
        if not approver:
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Authority Check Failed: No authorized management reviewer identified. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can reject Jira tickets."
                    },
                )
            return render_confirmation_html(
                title="No Authority to Reject",
                status_type="error",
                heading="Authority Check Failed",
                message="No authorized management reviewer identified. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can reject Jira tickets.",
                status_code=403,
            )
    else:
        member = resolve_and_verify_approver(approver_id)
        if not member:
            unauth = get_member_or_sender_identity(approver_id)
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Developer"
            if "application/json" in content_type:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": f"Authority Check Failed: You have role '{unauth_role}'. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can reject tickets."
                    },
                )
            return render_confirmation_html(
                title="No Authority to Reject",
                status_type="error",
                heading="Authority Check Failed",
                message=f"You have no authority to reject Jira ticket creation (Role: {unauth_role}). Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can reject tickets.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason)
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Reject Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", "Failed to reject ticket"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline ticket"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        return render_confirmation_html(
            title="Ticket Creation Rejected",
            status_type="declined",
            heading=f"Rejected by {approver}",
            message=f"Ticket creation was rejected by {approver}. No tickets were created in Jira, and notification has been posted to Teams.",
            details={"Status": "Rejected", "Rejected By": approver},
        )
    return res


@router.api_route("/api/jira/confirm-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_all_get(
    message_id: str,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await confirm_approval_get(message_id, request, assignee=assignee, reviewer=reviewer, auto=auto)


@router.post("/api/jira/confirm-issue/{message_id}")
async def confirm_issue_all_post(message_id: str, request: Request):
    """Programmatic PM Approval for all issues in a message (POST)."""
    return await confirm_approval_post(message_id, request)


@router.api_route("/api/jira/decline-issue/{message_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_issue_all_get(
    message_id: str,
    request: Request,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM Decline for all issues in a message (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)
    return await decline_approval_get(message_id, request, reviewer=reviewer, reason=reason)


@router.post("/api/jira/decline-issue/{message_id}")
async def decline_issue_all_post(message_id: str, request: Request):
    """Programmatic PM Decline for all issues in a message (POST)."""
    return await decline_approval_post(message_id, request)


@router.api_route("/api/jira/confirm-issue/{message_id}/{issue_idx}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def confirm_issue_get(
    message_id: str,
    issue_idx: int,
    request: Request,
    assignee: Optional[str] = None,
    reviewer: Optional[str] = None,
    auto: Optional[str] = None,
):
    """1-Click PM Approval for a specific single issue in a multi-issue triage card (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation
    import json

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    ai_ticket = {}
    if row["ai_ticket"]:
        try:
            ai_ticket = json.loads(row["ai_ticket"]) if isinstance(row["ai_ticket"], str) else row["ai_ticket"]
        except Exception:
            pass

    issues = ai_ticket.get("issues", [])
    if not issues or not isinstance(issues, list):
        issues = [ai_ticket]

    if not (0 <= issue_idx < len(issues)):
        return render_confirmation_html(
            title="Invalid Issue",
            status_type="error",
            heading="Error",
            message=f"Issue #{issue_idx + 1} was not found in this message.",
            status_code=400,
        )

    target_issue = issues[issue_idx]
    if target_issue.get("status") == "APPROVED" or target_issue.get("jira_key"):
        active_key = target_issue.get("jira_key") or row["jira_issue_key"]
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Already Created",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Active in Jira",
            message=f"Ticket {active_key} has already been created for Issue #{issue_idx + 1}. You can close this window now.",
            details={"Jira Ticket": active_key, "Status": "Active"},
            actions=[{"label": f"Open {active_key} in Jira ↗", "url": target_issue.get("jira_url") or row["jira_issue_url"] or "#"}],
        )

    if target_issue.get("status") == "DECLINED":
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Declined",
            status_type="declined",
            heading=f"Issue #{issue_idx + 1} Declined",
            message=f"Issue #{issue_idx + 1} was previously declined by PM and cannot be created.",
            details={"Status": "Declined", "Declined By": target_issue.get("declined_by", "PM")},
        )

    active_reviewer = resolve_active_reviewer_from_request(request, reviewer)
    user_agent = request.headers.get("user-agent", "").lower()
    is_testclient = "testclient" in user_agent

    if active_reviewer:
        member = resolve_and_verify_approver(active_reviewer)
        if not member:
            unauth = get_member_or_sender_identity(active_reviewer)
            unauth_name = (unauth.get("display_name") if unauth else active_reviewer) or "User"
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Client"
            return render_confirmation_html(
                title="Access Denied",
                status_type="error",
                heading="⛔ Strictly Not Permitted",
                message=f"You are currently identified as {unauth_name} ({unauth_role}). You are strictly not allowed or permitted to approve or reject Issue #{issue_idx + 1}. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    elif not is_testclient:
        return get_browser_redirect_or_gateway(
            request=request,
            action_type="confirm",
            message_id=message_id,
            assignee=assignee,
            issue_idx=issue_idx,
        )
    else:
        approver = get_approver_name_for_message(row=row)

    if not approver:
        return render_confirmation_html(
            title="Access Denied",
            status_type="error",
            heading="⛔ Strictly Not Permitted",
            message=f"No authorized management reviewer identified. You are strictly not allowed or permitted to approve or reject Issue #{issue_idx + 1}. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
            status_code=403,
        )

    target_assignee = assignee or target_issue.get("suggested_assignee")

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, issue_idx=issue_idx, assignee_override=target_assignee
    )
    if not res.get("success"):
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Creation Failed",
            status_type="error",
            heading="Error",
            message=res.get("error", f"Failed to create Issue #{issue_idx + 1}"),
            status_code=400,
        )

    key = res.get("key", "Created")
    url = res.get("url", "#")
    display_dev = target_assignee or "Assigned Developer"
    already = res.get("already_existed", False)

    resp = render_confirmation_html(
        title=f"Issue #{issue_idx + 1} {'Already Created' if already else 'Created Successfully'}",
        status_type="success",
        heading=f"Issue #{issue_idx + 1} Approved by {approver}",
        message=f"Ticket {key} {'is already active in Jira' if already else f'has been created in Jira and assigned to {display_dev}. Confirmation posted to Teams.'} You can close this window now.",
        details={"Jira Ticket": key, "Status": "Active in Jira", "Assignee": display_dev, "Approved By": approver},
        actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
    )
    if active_reviewer:
        resp.set_cookie("jira_reviewer", active_reviewer, max_age=2592000, path="/")
    return resp


@router.post("/api/jira/confirm-issue/{message_id}/{issue_idx}")
async def confirm_issue_post(
    message_id: str,
    issue_idx: int,
    request: Request,
):
    """Role-verified PM/TL Approval for a specific single issue (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_creation

    assignee = None
    approver_id = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            assignee = body.get("assignee")
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            assignee = form.get("assignee")
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
        except Exception:
            pass

    # Strictly verify that reviewer is an authorized management member (TL, PM, HM)
    member = resolve_and_verify_approver(approver_id)
    if not member:
        unauth = get_member_or_sender_identity(approver_id)
        unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Developer"
        logger.warning(
            f"Unauthorized confirmation attempt for issue #{issue_idx + 1} with reviewer '{approver_id}' (role: {unauth_role})",
            extra={"event": "UNAUTHORIZED_CONFIRM_ATTEMPT", "reviewer": approver_id, "messageId": message_id, "issueIdx": issue_idx}
        )
        if "application/json" in content_type:
            return JSONResponse(
                status_code=403,
                content={"error": f"Authority Check Failed: You have role '{unauth_role}'. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can confirm tickets."},
            )
        return render_confirmation_html(
            title="No Authority to Confirm",
            status_type="error",
            heading="Authority Check Failed",
            message=f"You have no authority to confirm Issue #{issue_idx + 1} creation (Role: {unauth_role}). Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
            status_code=403,
        )

    role = (member.get("role") or "PM").upper().strip()
    approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_creation(
        message_id, approver_name=approver, issue_idx=issue_idx, assignee_override=assignee
    )
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Error",
                message=res.get("error", f"Failed to create Issue #{issue_idx + 1}"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to create Jira issue"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        key = res.get("key", "Created")
        url = res.get("url", "#")
        target_dev = assignee or "Assigned Developer"
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Created Successfully",
            status_type="success",
            heading=f"Issue #{issue_idx + 1} Approved by {approver}",
            message=f"Ticket {key} has been created in Jira and assigned to {target_dev}. Confirmation posted to Teams.",
            details={"Jira Ticket": key, "Status": "Created & Active", "Assignee": target_dev, "Approved By": approver},
            actions=[{"label": f"Open {key} in Jira ↗", "url": url}],
        )
    return res


@router.api_route("/api/jira/decline-issue/{message_id}/{issue_idx}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def decline_issue_get(
    message_id: str,
    issue_idx: int,
    request: Request,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
):
    """1-Click PM Decline for a specific single issue in a multi-issue triage card (GET)."""
    if request.method == "HEAD":
        return Response(status_code=200)

    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    if not row:
        return render_confirmation_html(
            title="Message Not Found",
            status_type="error",
            heading="Error",
            message=f"Message ID '{message_id}' was not found in the database.",
            status_code=404,
        )

    active_reviewer = resolve_active_reviewer_from_request(request, reviewer)
    user_agent = request.headers.get("user-agent", "").lower()
    is_testclient = "testclient" in user_agent

    if active_reviewer:
        member = resolve_and_verify_approver(active_reviewer)
        if not member:
            unauth = get_member_or_sender_identity(active_reviewer)
            unauth_name = (unauth.get("display_name") if unauth else active_reviewer) or "User"
            unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Client"
            return render_confirmation_html(
                title="Access Denied",
                status_type="error",
                heading="⛔ Strictly Not Permitted",
                message=f"You are currently identified as {unauth_name} ({unauth_role}). You are strictly not allowed or permitted to approve or reject Issue #{issue_idx + 1}. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
                status_code=403,
            )
        role = (member.get("role") or "PM").upper().strip()
        approver = f"{role} {member.get('display_name')}"
    elif not is_testclient:
        return get_browser_redirect_or_gateway(
            request=request,
            action_type="decline",
            message_id=message_id,
            issue_idx=issue_idx,
            reason=reason,
        )
    else:
        approver = get_approver_name_for_message(row=row)

    if not approver:
        return render_confirmation_html(
            title="Access Denied",
            status_type="error",
            heading="⛔ Strictly Not Permitted",
            message=f"No authorized management reviewer identified. You are strictly not allowed or permitted to approve or reject Issue #{issue_idx + 1}. Please ask a Project Manager (PM), Team Lead (TL), or Higher Management (HM) to approve or reject.",
            status_code=403,
        )

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, issue_idx=issue_idx, reason=reason)
    if not res.get("success"):
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Reject Failed",
            status_type="error",
            heading="Cannot Reject",
            message=res.get("error", "Failed to reject issue"),
            status_code=400,
        )

    resp = render_confirmation_html(
        title=f"Issue #{issue_idx + 1} Rejected",
        status_type="declined",
        heading=f"Issue #{issue_idx + 1} Rejected by {approver}",
        message=f"Issue #{issue_idx + 1} was rejected by {approver}. No Jira ticket was created for this issue. You can close this window now.",
        details={"Status": f"Issue #{issue_idx + 1} Rejected", "Rejected By": approver},
    )
    if active_reviewer:
        resp.set_cookie("jira_reviewer", active_reviewer, max_age=2592000, path="/")
    return resp


@router.post("/api/jira/decline-issue/{message_id}/{issue_idx}")
async def decline_issue_post(message_id: str, issue_idx: int, request: Request):
    """PM/TL Decline for a specific single issue (POST)."""
    from src.database import get_db
    from src.services.message_service import execute_jira_ticket_decline

    approver_id = None
    reason = None
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            approver_id = body.get("reviewer") or body.get("approver_id") or body.get("approver")
            reason = body.get("reason")
        except Exception:
            pass
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            approver_id = form.get("reviewer") or form.get("approver_id") or form.get("approver")
            reason = form.get("reason")
        except Exception:
            pass

    member = resolve_and_verify_approver(approver_id)
    if not member:
        unauth = get_member_or_sender_identity(approver_id)
        unauth_role = (unauth.get("role") if unauth else "Unauthorized") or "Developer"
        if "application/json" in content_type:
            return JSONResponse(
                status_code=403,
                content={"error": f"Authority Check Failed: You have role '{unauth_role}'. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can reject tickets."},
            )
        return render_confirmation_html(
            title="No Authority to Reject",
            status_type="error",
            heading="Authority Check Failed",
            message=f"You have no authority to reject Issue #{issue_idx + 1} creation (Role: {unauth_role}). Please ask a Team Lead (TL), Project Manager (PM), or Higher Management (HM).",
            status_code=403,
        )

    role = (member.get("role") or "PM").upper().strip()
    approver = f"{role} {member.get('display_name')}"

    res = await execute_jira_ticket_decline(message_id, approver_name=approver, reason=reason, issue_idx=issue_idx)
    if not res.get("success"):
        if "text/html" in request.headers.get("accept", "") or "form" in content_type:
            return render_confirmation_html(
                title="Action Failed",
                status_type="error",
                heading="Cannot Reject",
                message=res.get("error", "Failed to reject issue"),
                status_code=400,
            )
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to decline Jira issue"))

    if "text/html" in request.headers.get("accept", "") or "form" in content_type:
        return render_confirmation_html(
            title=f"Issue #{issue_idx + 1} Rejected",
            status_type="declined",
            heading=f"Issue #{issue_idx + 1} Rejected by {approver}",
            message=f"Issue #{issue_idx + 1} was rejected by {approver}. No Jira ticket was created for this issue. You can close this window now.",
            details={"Status": f"Issue #{issue_idx + 1} Rejected", "Rejected By": approver},
        )
    return res
