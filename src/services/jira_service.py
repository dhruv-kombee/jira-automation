"""Jira Cloud Integration Service.

Interacts with Atlassian Jira Cloud REST API (v2 / v3) to verify credentials,
inspect projects, and automatically create structured Jira tickets from Teams messages.
"""
from typing import Any, Dict, List, Optional
import httpx
from src.config import config
from src.logger import logger
from src.database import get_db


def _get_auth():
    """Return Basic Auth tuple for HTTPX."""
    return (config.jira.email, config.jira.api_token)


def _get_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def text_to_adf(text: str) -> Dict[str, Any]:
    """Convert raw text or markdown into Atlassian Document Format (ADF) for Jira API v3."""
    lines = text.split("\n")
    paragraphs = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": stripped}],
            })
    if not paragraphs:
        paragraphs = [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "Reported via Microsoft Teams integration."}],
        }]
    return {
        "version": 1,
        "type": "doc",
        "content": paragraphs,
    }


async def test_jira_connection() -> Dict[str, Any]:
    """Test Jira credentials and project accessibility."""
    if not config.jira.is_configured:
        missing = []
        if not config.jira.base_url:
            missing.append("JIRA_BASE_URL")
        if not config.jira.email:
            missing.append("JIRA_EMAIL")
        if not config.jira.api_token:
            missing.append("JIRA_API_TOKEN")
        if not config.jira.project_key:
            missing.append("JIRA_PROJECT_KEY")
        return {
            "configured": False,
            "connected": False,
            "missing": missing,
            "message": f"Missing Jira environment variables: {', '.join(missing)}",
        }

    base_url = config.jira.base_url
    auth = _get_auth()
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Test authentication with /myself
        try:
            myself_res = await client.get(f"{base_url}/rest/api/3/myself", auth=auth, headers=headers)
        except Exception as net_err:
            return {
                "configured": True,
                "connected": False,
                "error": f"Failed to connect to Jira host '{base_url}': {net_err}",
            }

        if myself_res.status_code == 401:
            return {
                "configured": True,
                "connected": False,
                "error": "Authentication failed (401 Unauthorized). Please check JIRA_EMAIL and JIRA_API_TOKEN.",
            }
        elif myself_res.status_code != 200:
            return {
                "configured": True,
                "connected": False,
                "error": f"Jira returned HTTP {myself_res.status_code}: {myself_res.text[:200]}",
            }

        user_data = myself_res.json()
        display_name = user_data.get("displayName")
        account_email = user_data.get("emailAddress", config.jira.email)

        # 2. Test project accessibility
        project_key = config.jira.project_key
        project_data = {}
        issue_types = []

        try:
            proj_res = await client.get(f"{base_url}/rest/api/3/project/{project_key}", auth=auth, headers=headers)
            if proj_res.status_code == 200:
                project_data = proj_res.json()
                issue_types = [it.get("name") for it in project_data.get("issueTypes", []) if it.get("name")]
            elif proj_res.status_code == 404:
                return {
                    "configured": True,
                    "connected": True,
                    "project_valid": False,
                    "user": {"name": display_name, "email": account_email},
                    "error": f"Project key '{project_key}' was not found. Please verify JIRA_PROJECT_KEY.",
                }
        except Exception as proj_err:
            logger.warning(f"Failed to fetch project details: {proj_err}")

        return {
            "configured": True,
            "connected": True,
            "project_valid": True,
            "user": {
                "name": display_name,
                "email": account_email,
            },
            "project": {
                "key": project_key,
                "name": project_data.get("name", project_key),
                "issue_types": issue_types,
            },
            "message": f"Successfully connected to Jira as {display_name} ({account_email}) for project {project_key}!",
        }


async def create_jira_issue(
    summary: str,
    description: str,
    issue_type: str = "Bug",
    priority: str = "Medium",
    labels: Optional[List[str]] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an issue in Jira Cloud from extracted ticket data."""
    if not config.jira.is_configured:
        return {
            "success": False,
            "error": "Jira is not configured. Please set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, and JIRA_PROJECT_KEY in .env.",
        }

    base_url = config.jira.base_url
    project_key = config.jira.project_key
    auth = _get_auth()
    headers = _get_headers()

    clean_summary = summary.strip().replace("\n", " ")
    if len(clean_summary) > 250:
        clean_summary = clean_summary[:247] + "..."

    labels_list = labels or ["teams-automation"]
    # Jira label values cannot have spaces
    sanitized_labels = [re_label.replace(" ", "-").replace("/", "-") for re_label in labels_list if re_label]

    # Map priority if needed
    valid_priorities = ["Highest", "High", "Medium", "Low", "Lowest"]
    safe_priority = priority if priority in valid_priorities else "Medium"

    # Step 1: Try Jira REST API v2 (supports plain text / markdown description)
    v2_payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": clean_summary,
            "description": description or "Issue reported from Microsoft Teams",
            "issuetype": {"name": issue_type},
            "priority": {"name": safe_priority},
            "labels": sanitized_labels,
        }
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.post(
                f"{base_url}/rest/api/2/issue",
                json=v2_payload,
                auth=auth,
                headers=headers,
            )
        except Exception as net_err:
            return {"success": False, "error": f"Network error calling Jira: {net_err}"}

        # If v2 requires ADF or returns 400 with description schema error, retry with v3 ADF
        if res.status_code == 400 and ("Atlassian Document Format" in res.text or "description" in res.text):
            logger.info("Jira v2 rejected plain description, retrying with API v3 ADF format")
            v3_payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": clean_summary,
                    "description": text_to_adf(description),
                    "issuetype": {"name": issue_type},
                    "priority": {"name": safe_priority},
                    "labels": sanitized_labels,
                }
            }
            res = await client.post(
                f"{base_url}/rest/api/3/issue",
                json=v3_payload,
                auth=auth,
                headers=headers,
            )

        # If issue type rejected (e.g. project does not have "Bug", only "Task"), retry with default issue type
        if res.status_code == 400 and "issuetype" in res.text.lower() and issue_type != config.jira.default_issue_type:
            logger.warning(f"Issue type '{issue_type}' rejected, falling back to default '{config.jira.default_issue_type}'")
            v2_payload["fields"]["issuetype"] = {"name": config.jira.default_issue_type}
            res = await client.post(
                f"{base_url}/rest/api/2/issue",
                json=v2_payload,
                auth=auth,
                headers=headers,
            )

        if res.status_code not in (200, 201):
            logger.error(
                f"Failed to create Jira issue: HTTP {res.status_code} - {res.text}",
                extra={"event": "JIRA_CREATE_FAILED", "status": res.status_code, "body": res.text[:300]},
            )
            return {
                "success": False,
                "status_code": res.status_code,
                "error": f"Jira error ({res.status_code}): {res.text}",
            }

        data = res.json()
        issue_key = data.get("key")
        issue_id = data.get("id")
        issue_url = f"{base_url}/browse/{issue_key}"

        logger.info(
            f"Jira issue created successfully: {issue_key}",
            extra={"event": "JIRA_TICKET_CREATED", "issueKey": issue_key, "issueUrl": issue_url},
        )

        # Update database if message_id is provided
        if message_id:
            try:
                db = get_db()
                db.execute(
                    "UPDATE messages SET jira_issue_key = ?, jira_issue_url = ? WHERE message_id = ?",
                    (issue_key, issue_url, message_id),
                )
            except Exception as db_err:
                logger.warning(f"Could not update message with Jira key in DB: {db_err}")

        # Broadcast update to web dashboard
        try:
            from src.services.broadcaster import broadcast_message
            await broadcast_message({
                "type": "JIRA_TICKET_CREATED",
                "messageId": message_id,
                "issueKey": issue_key,
                "issueUrl": issue_url,
                "summary": clean_summary,
            })
        except Exception:
            pass

        return {
            "success": True,
            "key": issue_key,
            "id": issue_id,
            "url": issue_url,
            "summary": clean_summary,
        }
