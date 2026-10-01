"""AI Ticket Extraction Service using Google Gemini.

Extracts structured Jira issue specifications from raw Microsoft Teams messages.
Integrates with the official google-genai SDK with rule-based heuristic fallback.
"""
from typing import Any, Dict, List, Optional
import json
import re
from pydantic import BaseModel, Field

from src.config import config
from src.logger import logger


class JiraTicketDraft(BaseModel):
    is_ticket_request: bool = Field(
        default=True,
        description="Whether this message represents an issue, bug, task, or request that should become a Jira ticket",
    )
    summary: str = Field(
        description="Concise, actionable, professional Jira issue title/summary (max 80 chars)",
    )
    issue_type: str = Field(
        default="Bug",
        description="Jira issue type: Bug, Task, Story, or Improvement",
    )
    priority: str = Field(
        default="Medium",
        description="Jira priority level: Highest, High, Medium, Low",
    )
    description: str = Field(
        description="Clean, well-structured description in markdown formatting with context and details",
    )
    suggested_assignee: Optional[str] = Field(
        default=None,
        description="Name of suggested developer (e.g. Musaib Khan) or null",
    )
    labels: List[str] = Field(
        default_factory=lambda: ["teams-automation", "client-reported"],
        description="Relevant Jira labels",
    )
    confidence: float = Field(
        default=0.95,
        description="Confidence score between 0.0 and 1.0",
    )


SYSTEM_PROMPT = """You are an expert Agile Scrum Master and Jira Technical Analyst.
Your task is to analyze incoming messages from a Microsoft Teams client support group chat and convert ticket requests into structured Jira issue specifications.

Rules:
1. Detect whether the message is asking for a bug fix, issue, feature, or action.
   - If it contains hashtags like #issue, #bug, #task or describes broken behavior, bugs, or questions: is_ticket_request = true.
   - If it is purely conversational greetings (e.g., "Hello everyone", "Thanks"): is_ticket_request = false.
2. Summary:
   - Provide a concise, clear, professional Jira title (e.g., "[Dashboard] Bug found across entire dashboard page").
3. Issue Type:
   - "Bug" for defects, broken features, unexpected behavior, visual glitches.
   - "Task" for general work, configuration, access requests.
   - "Story" for new feature requests.
4. Priority:
   - "Highest" or "High" if affecting the entire page, blocking users, or causing downtime.
   - "Medium" for standard bugs/tasks.
   - "Low" for minor cosmetic tweaks.
5. Description:
   - Format cleanly in Markdown with sections:
     - **Reported By**: Sender name & role
     - **Summary of Issue**: Clean explanation
     - **Observed Behavior**: What went wrong
     - **Acceptance Criteria**: What needs to happen to resolve it
6. Assignee:
   - If the message mentions a developer name or variant (e.g. "assignee musain", "musaib"), set suggested_assignee to "Musaib Khan".
"""


def _rule_based_fallback(
    text: str, sender_name: Optional[str] = None, sender_role: Optional[str] = None
) -> Dict[str, Any]:
    """Smart fallback parser when Gemini API key is not configured or offline."""
    clean_text = text.strip()
    lower_text = clean_text.lower()

    # Detect if ticket request
    is_ticket = any(kw in lower_text for kw in ["#issue", "#bug", "#task", "bug", "issue", "error", "fix", "fail", "broken"])

    # Determine type
    issue_type = "Bug" if any(w in lower_text for w in ["bug", "error", "broken", "failed", "crash"]) else "Task"

    # Determine priority
    priority = "High" if any(w in lower_text for w in ["whole page", "entire", "urgent", "blocking", "critical", "crash"]) else "Medium"

    # Clean title
    title_text = re.sub(r'#\w+', '', clean_text).strip()
    lines = [line.strip() for line in title_text.splitlines() if line.strip()]
    raw_title = lines[0] if lines else "Teams Issue Report"
    raw_title = raw_title.replace('"', '').replace("'", "")
    if len(raw_title) > 65:
        raw_title = raw_title[:62] + "..."

    summary = f"[{issue_type}] {raw_title}"

    # Suggested assignee
    assignee = None
    if "musaib" in lower_text or "musain" in lower_text:
        assignee = "Musaib Khan"

    description = f"""### Reported Issue
**Reporter**: {sender_name or 'Client'} ({sender_role or 'CLIENT'})

**Raw Teams Message**:
> {clean_text}

### Details & Investigation
- **Context**: Captured automatically from Microsoft Teams integration.
- **Identified Type**: {issue_type}
- **Assigned Target**: {assignee or 'Unassigned (Awaiting PM Triage)'}
"""

    return {
        "is_ticket_request": is_ticket,
        "summary": summary,
        "issue_type": issue_type,
        "priority": priority,
        "description": description.strip(),
        "suggested_assignee": assignee,
        "labels": ["teams-automation", "client-reported", issue_type.lower()],
        "confidence": 0.85,
        "extractor": "heuristic_fallback",
    }


async def extract_jira_ticket(
    text: str,
    sender_name: Optional[str] = None,
    sender_role: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract structured Jira ticket fields from a Teams message using Gemini or heuristic fallback."""
    if not text or not text.strip():
        return {
            "is_ticket_request": False,
            "summary": "Empty message",
            "issue_type": "Task",
            "priority": "Low",
            "description": "",
            "suggested_assignee": None,
            "labels": [],
            "confidence": 0.0,
            "extractor": "none",
        }

    api_key = config.gemini.api_key
    if not api_key:
        logger.info(
            "GEMINI_API_KEY not configured in .env, using smart heuristic ticket extractor",
            extra={"event": "AI_EXTRACT_HEURISTIC"},
        )
        return _rule_based_fallback(text, sender_name=sender_name, sender_role=sender_role)

    # Use Google Gemini SDK
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        user_prompt = f"""Sender: {sender_name or 'Client'} (Role: {sender_role or 'CLIENT'})
Message Content:
\"\"\"
{text}
\"\"\"

Analyze this message and extract the Jira ticket draft in JSON format."""

        import asyncio

        def _call_gemini():
            return client.models.generate_content(
                model=config.gemini.model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=JiraTicketDraft,
                    temperature=0.2,
                ),
            )

        response = await asyncio.to_thread(_call_gemini)

        parsed = json.loads(response.text)
        if "labels" in parsed and isinstance(parsed["labels"], list):
            if "teams-automation" not in parsed["labels"]:
                parsed["labels"].append("teams-automation")
        else:
            parsed["labels"] = ["teams-automation"]

        parsed["extractor"] = f"gemini ({config.gemini.model})"
        logger.info(
            f"AI ticket successfully extracted with {config.gemini.model}",
            extra={"event": "AI_TICKET_EXTRACTED", "summary": parsed.get("summary")},
        )
        return parsed

    except Exception as err:
        logger.warning(
            f"Gemini API call failed ({err}), falling back to heuristic extractor",
            extra={"event": "AI_EXTRACT_ERROR", "error": str(err)},
        )
        fallback = _rule_based_fallback(text, sender_name=sender_name, sender_role=sender_role)
        fallback["extractor"] = "heuristic_fallback (api_error)"
        return fallback
