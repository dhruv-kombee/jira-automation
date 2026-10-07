"""Jira Cloud Integration Service.

Interacts with Atlassian Jira Cloud REST API (v2 / v3) to verify credentials,
inspect projects, and automatically create structured Jira tickets from Teams messages.
"""
import re
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


def strip_markdown_asterisks(text: Optional[str]) -> str:
    """Strip all raw markdown asterisks (* and **) and underscores from text while keeping the readable text intact."""
    if not text:
        return ""
    # Remove bold/italic markdown markers: **text** -> text, *text* -> text, __text__ -> text, _text_ -> text
    cleaned = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", str(text))
    cleaned = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", cleaned)
    # Remove any stray asterisks or leading bullet asterisks
    cleaned = re.sub(r"^\s*\*\s+", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("*", "")
    return cleaned.strip()


def build_jira_adf(
    summary: str,
    priority: str = "Medium",
    reporter_name: Optional[str] = None,
    reporter_role: Optional[str] = None,
    module: Optional[str] = None,
    observed_behavior: Optional[str] = None,
    expected_behavior: Optional[str] = None,
    steps_to_reproduce: Optional[List[str]] = None,
    evidence: Optional[List[str]] = None,
    acceptance_criteria: Optional[List[str]] = None,
    suggested_assignee: Optional[str] = None,
    raw_message: Optional[str] = None,
    fallback_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct an Atlassian Document Format (ADF) document for Jira Cloud API v3.
    Produces a professional defect/task ticket with an info callout panel, level 3 headings,
    and bullet lists, guaranteed to have zero raw asterisks.
    """
    content: List[Dict[str, Any]] = []

    # 1. Info Callout Panel at Top
    rep_name = strip_markdown_asterisks(reporter_name or "Client")
    rep_role = strip_markdown_asterisks(reporter_role or "CLIENT")
    mod_name = strip_markdown_asterisks(module or "General")
    safe_priority = strip_markdown_asterisks(priority)

    panel_text_elements = [
        {"type": "text", "text": "Reporter: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{rep_name} ({rep_role})\n"},
        {"type": "text", "text": "Module: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{mod_name}\n"},
        {"type": "text", "text": "Priority: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": f"{safe_priority}\n"},
    ]

    if suggested_assignee:
        clean_assignee = strip_markdown_asterisks(suggested_assignee)
        panel_text_elements.extend([
            {"type": "text", "text": "Suggested Specialist: ", "marks": [{"type": "strong"}]},
            {"type": "text", "text": f"{clean_assignee}\n"},
        ])

    panel_text_elements.extend([
        {"type": "text", "text": "Source: ", "marks": [{"type": "strong"}]},
        {"type": "text", "text": "Microsoft Teams Support Automation"},
    ])

    content.append({
        "type": "panel",
        "attrs": {"panelType": "info"},
        "content": [{"type": "paragraph", "content": panel_text_elements}],
    })

    # 2. Observed Behavior
    obs_text = strip_markdown_asterisks(observed_behavior or fallback_text or "")
    if obs_text:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Observed Behavior"}],
        })
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": obs_text}],
        })

    # 3. Expected Behavior
    exp_text = strip_markdown_asterisks(expected_behavior or "")
    if exp_text:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Expected Behavior"}],
        })
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": exp_text}],
        })

    # 4. Steps to Reproduce
    clean_steps = [strip_markdown_asterisks(s) for s in (steps_to_reproduce or []) if strip_markdown_asterisks(s)]
    if clean_steps:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Steps to Reproduce"}],
        })
        list_items = [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": step}]}],
            }
            for step in clean_steps
        ]
        content.append({"type": "bulletList", "content": list_items})

    # 5. Technical & Visual Evidence
    clean_evidence = [strip_markdown_asterisks(e) for e in (evidence or []) if strip_markdown_asterisks(e)]
    if clean_evidence:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Technical & Visual Evidence"}],
        })
        list_items = [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": ev}]}],
            }
            for ev in clean_evidence
        ]
        content.append({"type": "bulletList", "content": list_items})

    # 6. Acceptance Criteria
    clean_criteria = [strip_markdown_asterisks(c) for c in (acceptance_criteria or []) if strip_markdown_asterisks(c)]
    if clean_criteria:
        content.append({
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Acceptance Criteria"}],
        })
        list_items = [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": crit}]}],
            }
            for crit in clean_criteria
        ]
        content.append({"type": "bulletList", "content": list_items})

    # 7. Original Teams Communication (if distinct)
    if raw_message:
        clean_raw = strip_markdown_asterisks(raw_message)
        if clean_raw and clean_raw != obs_text:
            content.append({
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Original Teams Communication"}],
            })
            content.append({
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": clean_raw}]}],
            })

    if not content:
        content = [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "Reported via Microsoft Teams integration."}],
        }]

    return {
        "version": 1,
        "type": "doc",
        "content": content,
    }


def text_to_adf(text: str) -> Dict[str, Any]:
    """Convert raw text or markdown into Atlassian Document Format (ADF) for Jira API v3.
    Intelligently parses headings, bullet lists, blockquotes, and strips raw markdown asterisks.
    """
    if not text or not str(text).strip():
        return {
            "version": 1,
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Reported via Microsoft Teams integration."}]}],
        }

    lines = str(text).split("\n")
    content: List[Dict[str, Any]] = []
    current_bullet_list: Optional[Dict[str, Any]] = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            current_bullet_list = None
            continue

        # Bullet list item: - item, * item, • item, or 1. item
        bullet_match = re.match(r"^(?:[-*•]|\d+\.)\s+(.+)$", line)
        if bullet_match:
            item_text = strip_markdown_asterisks(bullet_match.group(1))
            if item_text:
                list_item = {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": item_text}]}],
                }
                if current_bullet_list is not None:
                    current_bullet_list["content"].append(list_item)
                else:
                    current_bullet_list = {"type": "bulletList", "content": [list_item]}
                    content.append(current_bullet_list)
            continue

        current_bullet_list = None

        # Standalone heading: ### Heading, ## Heading, *Heading*:, **Heading**:
        header_match = re.match(r"^(?:#{1,4}\s*|\*{1,2})([A-Za-z0-9\s&/_-]+?)(?:\*{1,2})?:?$", line)
        if header_match and len(line) < 60:
            h_text = strip_markdown_asterisks(header_match.group(1))
            if h_text:
                content.append({
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": h_text}],
                })
            continue

        # Section prefix in a single line, e.g. "*Observed Behavior*: The user clicks..."
        section_match = re.match(
            r"^(?:\*{1,2})?(Reported By|Summary|Observed Behavior|Expected Behavior|Steps to Reproduce|Technical Evidence|Evidence|Acceptance Criteria)(?:\*{1,2})?:\s*(.+)$",
            line,
            re.IGNORECASE,
        )
        if section_match:
            sec_title = strip_markdown_asterisks(section_match.group(1))
            sec_body = strip_markdown_asterisks(section_match.group(2))
            content.append({
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": sec_title}],
            })
            if sec_body:
                content.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": sec_body}],
                })
            continue

        # Blockquote: > text
        if line.startswith(">"):
            q_text = strip_markdown_asterisks(line.lstrip(">").strip())
            if q_text:
                content.append({
                    "type": "blockquote",
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": q_text}]}],
                })
            continue

        # Normal paragraph
        clean_text = strip_markdown_asterisks(line)
        if clean_text:
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": clean_text}],
            })

    if not content:
        content = [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "Reported via Microsoft Teams integration."}],
        }]

    return {
        "version": 1,
        "type": "doc",
        "content": content,
    }


_assignable_users_cache: Dict[str, List[Dict[str, Any]]] = {}


async def get_assignable_users(project_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch assignable users for a project from Jira Cloud."""
    pk = project_key or config.jira.project_key
    if not pk or not config.jira.is_configured:
        return []
    base_url = config.jira.base_url
    auth = _get_auth()
    headers = _get_headers()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{base_url}/rest/api/3/user/assignable/search?project={pk}",
                auth=auth,
                headers=headers,
            )
            if res.status_code == 200:
                users = res.json()
                _assignable_users_cache[pk] = users
                return users
    except Exception as err:
        logger.warning(f"Could not fetch assignable Jira users: {err}")
    return _assignable_users_cache.get(pk, [])


_resolved_assignee_cache: Dict[str, str] = {}


async def resolve_jira_assignee(name_or_query: Optional[str], project_key: Optional[str] = None) -> Optional[str]:
    """Resolve a developer name, email, or query to an Atlassian accountId."""
    if not name_or_query:
        return None
    raw_query = name_or_query.strip()
    if raw_query.lower() in ("unassigned", "none", ""):
        return None

    # Check cache first
    cache_key = raw_query.lower()
    if cache_key in _resolved_assignee_cache:
        return _resolved_assignee_cache[cache_key]

    clean_query = re.sub(r"\s+", " ", raw_query).strip()
    query_lower = clean_query.lower()

    # 1. Check assignable users in project
    users = await get_assignable_users(project_key)
    for u in users:
        acc_id = u.get("accountId")
        if acc_id == raw_query:
            _resolved_assignee_cache[cache_key] = acc_id
            return acc_id
        display = (u.get("displayName") or "").lower()
        email = (u.get("emailAddress") or "").lower()
        if query_lower == display or query_lower == email or query_lower in display or display in query_lower:
            _resolved_assignee_cache[cache_key] = acc_id
            return acc_id

    # 2. Check Member.xlsx to find their registered email and clean name
    member_email = None
    first_name = clean_query.split()[0] if clean_query else ""
    try:
        from src.services.member_sync_service import get_all_members_from_excel
        members = get_all_members_from_excel()
        for m in members:
            c_name = re.sub(r"\s+", " ", m.get("display_name", "")).strip().lower()
            if query_lower in c_name or c_name in query_lower or (first_name and first_name.lower() in c_name):
                member_email = m.get("email")
                break
    except Exception:
        pass

    # 3. Query Jira Cloud User Search API (/rest/api/3/user/search)
    search_terms = [clean_query]
    if member_email:
        search_terms.append(member_email)
    if first_name and first_name not in search_terms:
        search_terms.append(first_name)

    base_url = config.jira.base_url
    auth = _get_auth()
    headers = _get_headers()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for term in search_terms:
                res = await client.get(
                    f"{base_url}/rest/api/3/user/search?query={term}",
                    auth=auth,
                    headers=headers,
                )
                if res.status_code == 200:
                    found_users = res.json()
                    for u in found_users:
                        u_name = (u.get("displayName") or "").lower()
                        u_email = (u.get("emailAddress") or "").lower()
                        acc_id = u.get("accountId")
                        if (
                            query_lower in u_name
                            or u_name in query_lower
                            or (member_email and member_email.lower() == u_email)
                            or (first_name and first_name.lower() in u_name)
                        ):
                            if acc_id:
                                _resolved_assignee_cache[cache_key] = acc_id
                                return acc_id
    except Exception as err:
        logger.warning(f"Error querying Jira user search API for '{name_or_query}': {err}")

    return None


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
    description: Optional[str] = None,
    issue_type: str = "Bug",
    priority: str = "Medium",
    labels: Optional[List[str]] = None,
    message_id: Optional[str] = None,
    assignee_name: Optional[str] = None,
    ticket_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create an issue in Jira Cloud from extracted ticket data.
    Uses Jira API v3 with Atlassian Document Format (ADF) to render clean,
    professional tickets without raw asterisks, and resolves developer assignees.
    """
    if not config.jira.is_configured:
        return {
            "success": False,
            "error": "Jira is not configured. Please set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, and JIRA_PROJECT_KEY in .env.",
        }

    base_url = config.jira.base_url
    project_key = config.jira.project_key
    auth = _get_auth()
    headers = _get_headers()

    clean_summary = strip_markdown_asterisks(summary.strip().replace("\n", " "))
    if len(clean_summary) > 250:
        clean_summary = clean_summary[:247] + "..."

    labels_list = labels or ["teams-automation"]
    # Jira label values cannot have spaces or slashes
    sanitized_labels = [
        re_label.replace(" ", "-").replace("/", "-").replace("*", "")
        for re_label in labels_list
        if re_label
    ]

    # Map priority safely
    valid_priorities = ["Highest", "High", "Medium", "Low", "Lowest"]
    safe_priority = priority if priority in valid_priorities else "Medium"

    # Build ADF document
    if ticket_data and isinstance(ticket_data, dict):
        adf_description = build_jira_adf(
            summary=clean_summary,
            priority=safe_priority,
            reporter_name=ticket_data.get("reporter_name") or ticket_data.get("sender_name"),
            reporter_role=ticket_data.get("reporter_role") or ticket_data.get("sender_role"),
            module=ticket_data.get("affected_module"),
            observed_behavior=ticket_data.get("observed_behavior"),
            expected_behavior=ticket_data.get("expected_behavior"),
            steps_to_reproduce=ticket_data.get("steps_to_reproduce"),
            evidence=ticket_data.get("evidence"),
            acceptance_criteria=ticket_data.get("acceptance_criteria"),
            suggested_assignee=assignee_name or ticket_data.get("suggested_assignee"),
            raw_message=ticket_data.get("raw_message") or ticket_data.get("message_text"),
            fallback_text=description or ticket_data.get("description"),
        )
    else:
        adf_description = text_to_adf(description or "Issue reported from Microsoft Teams")

    # Resolve assignee accountId if provided
    target_assignee = assignee_name or (ticket_data.get("suggested_assignee") if ticket_data else None)
    resolved_account_id = None
    if target_assignee:
        try:
            resolved_account_id = await resolve_jira_assignee(target_assignee, project_key)
        except Exception as ass_err:
            logger.debug(f"Assignee resolution skipped ({ass_err})")

    # Primary: Jira Cloud REST API v3 (Native ADF document support)
    v3_payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": clean_summary,
            "description": adf_description,
            "issuetype": {"name": issue_type},
            "priority": {"name": safe_priority},
            "labels": sanitized_labels,
        }
    }
    if resolved_account_id:
        v3_payload["fields"]["assignee"] = {"accountId": resolved_account_id}

    async with httpx.AsyncClient(timeout=20.0) as client:
        res = None
        for attempt in range(3):
            try:
                res = await client.post(
                    f"{base_url}/rest/api/3/issue",
                    json=v3_payload,
                    auth=auth,
                    headers=headers,
                )
                if res.status_code in (429, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                break
            except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                return {"success": False, "error": f"Network error calling Jira (after retries): {net_err}"}
            except Exception as net_err:
                return {"success": False, "error": f"Network error calling Jira: {net_err}"}

        # If issue type rejected (e.g. project is SCRUM with Task, not Bug), retry with default_issue_type
        if res.status_code == 400 and ("issuetype" in res.text.lower() or "valid issue type" in res.text.lower()):
            if issue_type != config.jira.default_issue_type:
                logger.warning(
                    f"Issue type '{issue_type}' not available in Jira project '{project_key}', retrying with '{config.jira.default_issue_type}'"
                )
                v3_payload["fields"]["issuetype"] = {"name": config.jira.default_issue_type}
                res = await client.post(
                    f"{base_url}/rest/api/3/issue",
                    json=v3_payload,
                    auth=auth,
                    headers=headers,
                )

        # If assignee rejected (e.g. user cannot be assigned or permission issue), retry without assignee
        if res.status_code == 400 and "assignee" in res.text.lower() and "assignee" in v3_payload["fields"]:
            logger.warning("Assignee assignment rejected by Jira, retrying as unassigned...")
            del v3_payload["fields"]["assignee"]
            res = await client.post(
                f"{base_url}/rest/api/3/issue",
                json=v3_payload,
                auth=auth,
                headers=headers,
            )

        # Fallback to API v2 if API v3 is not supported on host
        if res.status_code == 404:
            logger.info("Jira API v3 not found, falling back to API v2 plain text")
            clean_desc = strip_markdown_asterisks(description or "Issue reported from Microsoft Teams")
            v2_payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": clean_summary,
                    "description": clean_desc,
                    "issuetype": {"name": config.jira.default_issue_type},
                    "priority": {"name": safe_priority},
                    "labels": sanitized_labels,
                }
            }
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


async def upload_jira_attachment(
    issue_key: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> Dict[str, Any]:
    """Upload a file or screenshot attachment directly to a Jira Cloud issue."""
    if not config.jira.is_configured:
        return {"success": False, "error": "Jira is not configured"}

    base_url = config.jira.base_url
    auth = _get_auth()
    headers = {
        "X-Atlassian-Token": "no-check",
    }

    url = f"{base_url}/rest/api/3/issue/{issue_key}/attachments"
    files = {
        "file": (filename, file_bytes, content_type)
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, auth=auth, headers=headers, files=files)
            if res.status_code in (200, 201):
                logger.info(f"Successfully uploaded attachment '{filename}' to Jira issue {issue_key}")
                return {"success": True, "result": res.json()}
            else:
                logger.warning(f"Failed to upload attachment to Jira: HTTP {res.status_code} - {res.text[:200]}")
                return {"success": False, "status": res.status_code, "error": res.text}
    except Exception as err:
        logger.error(f"Error uploading attachment to Jira: {err}")
        return {"success": False, "error": str(err)}
