import os
import re
import time
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx

from src.config import config, env_path
from src.database import get_db
from src.logger import logger


def mask_secret(val: Optional[str], keep_start: int = 6, keep_end: int = 4) -> str:
    """Mask a sensitive string for non-technical user display."""
    if not val:
        return ""
    val_str = str(val).strip()
    if len(val_str) <= (keep_start + keep_end):
        return "••••••••••••"
    return f"{val_str[:keep_start]}••••••••{val_str[-keep_end:]}"


def get_system_config(include_raw_secrets: bool = False) -> Dict[str, Any]:
    """Return all system configurations categorized with friendly labels."""
    gemini_keys = config.gemini.api_keys or []
    masked_keys = [
        {"index": idx + 1, "masked": mask_secret(k), "raw": k if include_raw_secrets else ""}
        for idx, k in enumerate(gemini_keys)
    ]

    return {
        "jira": {
            "baseUrl": config.jira.base_url,
            "email": config.jira.email,
            "projectKey": config.jira.project_key,
            "defaultIssueType": config.jira.default_issue_type,
            "isConfigured": config.jira.is_configured,
            "apiTokenMasked": mask_secret(config.jira.api_token),
            "apiTokenRaw": config.jira.api_token if include_raw_secrets else "",
            "hasToken": bool(config.jira.api_token),
        },
        "teams": {
            "chatId": config.teams.chat_id or "",
            "teamId": config.teams.team_id or "",
            "channelId": config.teams.channel_id or "",
            "webhookUrl": config.teams.webhook_url or "",
            "hasWebhook": bool(config.teams.webhook_url),
            "allowSelfApproval": getattr(config.roles, "allow_self_approval", True),
        },
        "microsoft": {
            "tenantId": config.microsoft.tenant_id or "",
            "clientId": config.microsoft.client_id or "",
            "clientSecretMasked": mask_secret(config.microsoft.client_secret),
            "clientSecretRaw": config.microsoft.client_secret if include_raw_secrets else "",
            "hasSecret": bool(config.microsoft.client_secret),
        },
        "gemini": {
            "model": config.gemini.model,
            "keys": masked_keys,
            "totalKeys": len(gemini_keys),
            "primaryKeyMasked": mask_secret(config.gemini.api_key),
            "hasKeys": bool(config.gemini.api_key),
        },
        "network": {
            "port": config.port,
            "webhookPublicUrl": config.webhook_public_url or "",
            "logLevel": config.log_level,
        },
    }


def update_env_file(updates: Dict[str, str], performed_by: str = "Admin") -> Dict[str, Any]:
    """Safely update .env file with new key-value pairs, making a backup first."""
    if not env_path.exists():
        env_path.touch()

    # 1. Create a timestamped backup of the current .env
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = env_path.parent / f".env.backup_{timestamp}"
    shutil.copy2(env_path, backup_path)

    # 2. Read existing content lines
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        # Preserve comments and empty lines
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue

        # Match KEY=VALUE
        eq_idx = line.find("=")
        if eq_idx != -1:
            key = line[:eq_idx].strip()
            if key in updates:
                new_val = updates[key]
                new_lines.append(f"{key}={new_val}\n")
                updated_keys.add(key)
                continue

        new_lines.append(line)

    # Append any brand new keys that weren't in the file
    for k, v in updates.items():
        if k not in updated_keys and v is not None:
            new_lines.append(f"{k}={v}\n")

    # 3. Write back to .env
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # 4. Reload in-memory config
    config.reload()

    # 5. Log audit trail to database
    try:
        db = get_db()
        db.execute(
            """
            INSERT INTO admin_audit_log (category, action, details, performed_by)
            VALUES (?, ?, ?, ?)
            """,
            ("CONFIG", "SETTINGS_UPDATED", f"Updated keys: {', '.join(updates.keys())}", performed_by),
        )
    except Exception as db_err:
        logger.debug(f"Audit log insertion failed: {db_err}")

    logger.info(
        f"Admin settings updated ({len(updates)} keys)",
        extra={"event": "ADMIN_CONFIG_UPDATED", "keys": list(updates.keys()), "backup": str(backup_path)},
    )

    return {
        "success": True,
        "updatedKeys": list(updates.keys()),
        "backupCreated": backup_path.name,
    }


async def test_jira_credentials(
    base_url: str,
    email: str,
    api_token: str,
    project_key: str,
) -> Dict[str, Any]:
    """Test Jira connection with given credentials without saving them first."""
    clean_url = (base_url or "").rstrip("/")
    if not clean_url or not email or not api_token or not project_key:
        return {
            "success": False,
            "error": "Please provide Jira URL, user email, API token, and project key.",
        }

    target_endpoint = f"{clean_url}/rest/api/3/project/{project_key.upper().strip()}"
    import base64

    auth_str = f"{email.strip()}:{api_token.strip()}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Accept": "application/json",
    }

    try:
        start_t = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(target_endpoint, headers=headers)
            latency = int((time.time() - start_t) * 1000)

            if res.status_code == 200:
                data = res.json()
                project_name = data.get("name", project_key)
                lead_name = (data.get("lead") or {}).get("displayName", "Project Admin")
                return {
                    "success": True,
                    "projectName": project_name,
                    "projectKey": project_key.upper().strip(),
                    "projectLead": lead_name,
                    "latencyMs": latency,
                    "message": f"Successfully connected to Jira Project '{project_name}' ({latency}ms).",
                }
            elif res.status_code == 401:
                return {
                    "success": False,
                    "error": "Authentication Failed (401): Please verify your Jira Email and API Token.",
                }
            elif res.status_code == 404:
                return {
                    "success": False,
                    "error": f"Project Not Found (404): Project key '{project_key}' does not exist on {clean_url}.",
                }
            else:
                return {
                    "success": False,
                    "error": f"Jira returned HTTP {res.status_code}: {res.text[:200]}",
                }
    except Exception as err:
        return {"success": False, "error": f"Connection error: {str(err)}"}


async def test_gemini_credentials(api_key: str, model: str = "gemini-3.5-flash-lite") -> Dict[str, Any]:
    """Test a Google Gemini API key live."""
    if not api_key:
        return {"success": False, "error": "No Gemini API key provided."}

    try:
        from google import genai
        import asyncio

        start_t = time.time()
        client = genai.Client(api_key=api_key.strip())

        def _ping():
            return client.models.generate_content(
                model=model,
                contents=["Respond with only the single word: OK"],
            )

        resp = await asyncio.to_thread(_ping)
        latency = int((time.time() - start_t) * 1000)
        return {
            "success": True,
            "model": model,
            "latencyMs": latency,
            "response": (resp.text or "").strip()[:50],
            "message": f"Gemini API key is active and responding ({latency}ms).",
        }
    except Exception as err:
        err_msg = str(err)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            return {"success": False, "error": "API Key rate limit reached (HTTP 429: Resource Exhausted)."}
        if "API_KEY_INVALID" in err_msg or "400" in err_msg:
            return {"success": False, "error": "Invalid API Key. Please verify the key in Google AI Studio."}
        return {"success": False, "error": f"Gemini test failed: {err_msg}"}


async def test_teams_webhook_payload(webhook_url: str) -> Dict[str, Any]:
    """Send a lightweight test verification card to a Microsoft Teams webhook URL."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL provided."}

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
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
                                    "text": "⚡ Teams-to-Jira Automation: Webhook Verified",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Good",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"Test notification sent successfully from Management Dashboard at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
                                    "wrap": True,
                                    "isSubtle": True,
                                },
                            ],
                        }
                    ],
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(webhook_url.strip(), json=payload)
            if res.status_code in (200, 201, 202):
                return {
                    "success": True,
                    "status": res.status_code,
                    "message": "Teams Webhook card posted successfully!",
                }
            return {
                "success": False,
                "error": f"Teams returned status {res.status_code}: {res.text[:200]}",
            }
    except Exception as err:
        return {"success": False, "error": f"Could not reach webhook URL: {str(err)}"}
