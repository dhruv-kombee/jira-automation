"""Microsoft 365 OAuth2 / SSO Authentication Router.

Provides zero-friction single sign-on (SSO) for Jira ticket approval & rejection workflows.
Verifies management identities (HM, PM, TL) cryptographically against Microsoft Entra ID
and Member.xlsx, eliminating spoofing and removing the manual 'Confirm Identity' selection screen.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.config import config
from src.logger import logger
from src.services.member_sync_service import get_all_members_from_excel
from src.services.sender_service import is_user_authorized_approver

router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie configuration
AUTH_COOKIE_NAME = "jira_auth_session"
AUTH_REVIEWER_COOKIE = "jira_reviewer"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30 days
SCOPES = ["User.Read"]


def get_cookie_signing_secret() -> bytes:
    """Return secret key used to cryptographically sign auth cookies."""
    secret = config.microsoft.client_secret or "jira-automation-sso-secret-salt-2026"
    return secret.encode("utf-8")


def create_signed_session_token(payload: Dict[str, Any]) -> str:
    """Create a tamper-proof HMAC-SHA256 signed session token."""
    payload_copy = dict(payload)
    if "exp" not in payload_copy:
        payload_copy["exp"] = int(time.time()) + COOKIE_MAX_AGE_SECONDS

    data_json = json.dumps(payload_copy, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_data = base64.urlsafe_b64encode(data_json).decode("utf-8").rstrip("=")
    sig = hmac.new(get_cookie_signing_secret(), b64_data.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64_data}.{sig}"


def verify_signed_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verify HMAC signature and expiration on a session token."""
    if not token or "." not in token:
        return None
    try:
        b64_data, sig = token.split(".", 1)
        expected_sig = hmac.new(get_cookie_signing_secret(), b64_data.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None

        # Add base64 padding back
        pad = len(b64_data) % 4
        padded = b64_data + ("=" * (4 - pad) if pad else "")
        data_json = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(data_json.decode("utf-8"))

        # Check expiration
        if payload.get("exp") and int(payload["exp"]) < int(time.time()):
            return None

        return payload
    except Exception as e:
        logger.debug(f"Failed to verify auth session token: {e}")
        return None


def get_authenticated_reviewer_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """Extract and verify logged-in manager identity from signed cookies."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    payload = verify_signed_session_token(token)
    if payload and payload.get("display_name"):
        return payload
    return None


def find_member_by_email_or_name(email: Optional[str], name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Match Microsoft Entra authenticated identity with team roster in Member.xlsx."""
    members = get_all_members_from_excel()
    clean_email = (email or "").strip().lower()
    clean_name = (name or "").strip().lower()

    if clean_email:
        for m in members:
            m_email = (m.get("email") or "").strip().lower()
            if m_email and m_email == clean_email:
                return m

    if clean_name:
        for m in members:
            m_name = (m.get("display_name") or "").strip().lower()
            if m_name and m_name == clean_name:
                return m

    return None


def render_sso_access_denied_html(name: str, role: str, email: str) -> HTMLResponse:
    """Render a secure Access Denied message when non-management tries to approve."""
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Access Denied — Role Authority</title>
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
      border: 1px solid #ef444455;
      border-radius: 14px;
      max-width: 480px;
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
      background: #ef444422;
      color: #f87171;
      border: 1px solid #ef444455;
      margin-bottom: 16px;
    }}
    h1 {{ font-size: 20px; margin: 0 0 10px 0; color: #f87171; }}
    p.subtext {{ color: #cbd5e1; font-size: 14px; line-height: 1.6; margin: 0 0 20px 0; }}
    .user-info {{
      background: #0f172a;
      border-radius: 8px;
      padding: 14px 16px;
      text-align: left;
      font-size: 13.5px;
      margin-bottom: 24px;
      border-left: 4px solid #ef4444;
    }}
    .footer-btn {{
      background: #334155;
      color: #f8fafc;
      border: 1px solid #475569;
      padding: 10px 24px;
      border-radius: 8px;
      cursor: pointer;
      font-weight: 600;
      font-size: 13.5px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">⛔ Strictly Not Permitted</div>
    <h1>Management Authority Required</h1>
    <p class="subtext">
      Only <strong>Project Managers (PM)</strong>, <strong>Team Leads (TL)</strong>, and <strong>Higher Management (HM)</strong> have authority to approve or reject Jira tickets.
    </p>
    <div class="user-info">
      <div><strong>Verified Microsoft Account:</strong> {email}</div>
      <div style="margin-top:6px;"><strong>Name & Role:</strong> {name} ({role})</div>
      <div style="margin-top:6px; color:#f87171;">⚠️ Developers and Clients cannot approve or reject tickets.</div>
    </div>
    <button class="footer-btn" onclick="window.close()">✕ Close Window</button>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_content, status_code=status.HTTP_403_FORBIDDEN)


@router.api_route("/login", methods=["GET", "HEAD"])
def login(request: Request, redirect_after: str = "/"):
    """Initiate Microsoft 365 OAuth2 Single Sign-On flow."""
    if request.method == "HEAD":
        return Response(status_code=status.HTTP_200_OK)

    # If user is already authenticated with a valid session, redirect immediately
    authed = get_authenticated_reviewer_from_request(request)
    if authed:
        return RedirectResponse(url=redirect_after, status_code=status.HTTP_302_FOUND)

    tenant_id = config.microsoft.tenant_id
    client_id = config.microsoft.client_id
    client_secret = config.microsoft.client_secret

    if not tenant_id or not client_id or not client_secret:
        return HTMLResponse(
            content="<h3>Microsoft Graph credentials are not configured in .env.</h3>",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Build redirect URI pointing back to /auth/callback
    from src.services.teams_notifier import get_app_base_url
    app_base = get_app_base_url()
    redirect_uri = f"{app_base}/auth/callback"

    import msal
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    msal_app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )

    state_payload = {"target": redirect_after, "ts": int(time.time())}
    state_str = base64.urlsafe_b64encode(json.dumps(state_payload).encode("utf-8")).decode("utf-8")

    auth_url = msal_app.get_authorization_request_url(
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state_str,
        prompt="select_account",  # Allows smooth one-click account selection
    )

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.api_route("/callback", methods=["GET", "HEAD"])
def auth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Handle Microsoft OAuth2 authorization code callback."""
    if request.method == "HEAD":
        return Response(status_code=status.HTTP_200_OK)
    if error:
        logger.warning(f"Microsoft SSO error callback: {error} - {error_description}")
        return HTMLResponse(
            content=f"<h3>Authentication Failed</h3><p>{error_description or error}</p>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not code:
        return HTMLResponse(
            content="<h3>Missing authorization code from Microsoft.</h3>",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    tenant_id = config.microsoft.tenant_id
    client_id = config.microsoft.client_id
    client_secret = config.microsoft.client_secret

    from src.services.teams_notifier import get_app_base_url
    app_base = get_app_base_url()
    redirect_uri = f"{app_base}/auth/callback"

    import msal
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    msal_app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )

    token_result = msal_app.acquire_token_by_authorization_code(
        code=code,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    if "error" in token_result:
        err_msg = token_result.get("error_description") or token_result.get("error")
        logger.error(f"Failed to acquire Microsoft token by code: {err_msg}")
        return HTMLResponse(
            content=f"<h3>Failed to sign in with Microsoft</h3><p>{err_msg}</p>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    id_claims = token_result.get("id_token_claims", {})
    email = (id_claims.get("preferred_username") or id_claims.get("email") or "").strip().lower()
    name = (id_claims.get("name") or "").strip()

    logger.info(f"Verified Microsoft SSO account: {name} <{email}>", extra={"event": "SSO_SUCCESS", "email": email, "account_name": name})

    # Resolve against Member.xlsx
    member = find_member_by_email_or_name(email=email, name=name)
    if not member:
        return HTMLResponse(
            content=f"""<div style="font-family:sans-serif; background:#0f172a; color:#f8fafc; padding:30px; text-align:center;">
              <h2 style="color:#f87171;">Account Not Found in Roster</h2>
              <p>Your Microsoft account (<strong>{email}</strong>) is not registered in the team roster (Member.xlsx).</p>
              <p>Please contact your Administrator or Project Manager.</p>
            </div>""",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    member_name = member.get("display_name") or name
    member_role = (member.get("role") or "DEVELOPER").upper().strip()
    can_approve = is_user_authorized_approver(user_id=member.get("user_id"), display_name=member_name, allow_client=False)

    if not can_approve or member_role not in ("TL", "PM", "HM"):
        return render_sso_access_denied_html(name=member_name, role=member_role, email=email)

    # Determine original redirect target
    target_url = "/"
    if state:
        try:
            pad = len(state) % 4
            padded = state + ("=" * (4 - pad) if pad else "")
            decoded_state = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            target_url = decoded_state.get("target") or "/"
        except Exception:
            target_url = "/"

    # Create signed session token
    session_data = {
        "email": email,
        "display_name": member_name,
        "role": member_role,
        "user_id": member.get("user_id") or email,
    }
    session_token = create_signed_session_token(session_data)

    response = RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=session_token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    # Also set jira_reviewer for backward compatibility
    response.set_cookie(
        key=AUTH_REVIEWER_COOKIE,
        value=member_name,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=False,
        samesite="lax",
    )
    return response


@router.get("/logout")
def logout(request: Request, redirect_to: str = "/"):
    """Log out and clear authentication cookies."""
    response = RedirectResponse(url=redirect_to, status_code=status.HTTP_302_FOUND)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(AUTH_REVIEWER_COOKIE, path="/")
    return response


@router.get("/status")
def auth_status(request: Request):
    """Check current authentication status and verified identity."""
    authed = get_authenticated_reviewer_from_request(request)
    if authed:
        return JSONResponse({"authenticated": True, "user": authed})
    return JSONResponse({"authenticated": False, "user": None})
