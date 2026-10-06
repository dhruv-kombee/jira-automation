"""Multimodal AI Ticket Extraction Service using Google Gemini.

Extracts structured Jira issue specifications from raw Microsoft Teams messages,
including formatted text, code blocks, logs, inline screenshots, and file attachments.
Features:
- Multi-Key API Pool with automatic round-robin rotation and failover on HTTP 429.
- Multimodal Vision & Log Analysis (inspects screenshots, stack traces, and errors).
- Grounded Issue Classification (CONFIRMED_ISSUE, POSSIBLE_ISSUE, GENERAL_MESSAGE).
- Smart Assignee Routing by @mention or module specialty.
- Comprehensive Offline Rule-Based Heuristic Fallback.
"""
from typing import Any, Dict, List, Optional
from enum import Enum
import asyncio
import json
import re
from pydantic import BaseModel, Field

from src.config import config
from src.logger import logger


class ClassificationState(str, Enum):
    CONFIRMED_ISSUE = "CONFIRMED_ISSUE"  # Clear defect/bug/task; ready for PM approval
    POSSIBLE_ISSUE = "POSSIBLE_ISSUE"    # Inconclusive/ambiguous; PM can still approve
    GENERAL_MESSAGE = "GENERAL_MESSAGE"  # General conversation/greeting; no action needed


class JiraTicketItem(BaseModel):
    summary: str = Field(
        description="Concise, actionable, professional Jira issue title (max 80 chars, e.g. '[Dashboard] Refresh button unresponsive')",
    )
    issue_type: str = Field(
        default="Bug",
        description="Jira issue type: Bug, Task, Story, or Improvement",
    )
    priority: str = Field(
        default="Medium",
        description="Jira priority level: Highest, High, Medium, Low",
    )
    priority_rationale: Optional[str] = Field(
        default=None,
        description="Why this priority was chosen",
    )
    affected_module: Optional[str] = Field(
        default="General",
        description="Frontend/UI, Backend/API, Database, Payment, Authentication, etc.",
    )
    observed_behavior: Optional[str] = Field(
        default=None,
        description="What failed or broken behavior occurred without asterisks",
    )
    expected_behavior: Optional[str] = Field(
        default=None,
        description="What should have occurred under standard operation without asterisks",
    )
    steps_to_reproduce: List[str] = Field(
        default_factory=list,
        description="Step-by-step reproduction sequence without markdown asterisks",
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Extracted error codes, visual red error banners, UI glitches, or stack trace lines without asterisks",
    )
    acceptance_criteria: List[str] = Field(
        default_factory=list,
        description="Clear conditions required to verify resolution without markdown asterisks",
    )
    description: str = Field(
        default="",
        description="Clean, well-structured description without markdown asterisks",
    )
    suggested_assignee: Optional[str] = Field(
        default=None,
        description="Name of suggested developer (e.g. Musaib Khan, Hemil Ghori) or null",
    )
    assignee_rationale: Optional[str] = Field(
        default=None,
        description="Reason for developer suggestion",
    )
    labels: List[str] = Field(
        default_factory=lambda: ["teams-automation"],
        description="Relevant Jira labels without spaces or asterisks",
    )


class JiraTicketDraft(BaseModel):
    is_ticket_request: bool = Field(
        default=True,
        description="Whether this message or attachment represents an issue, bug, task, or request that should become a Jira ticket",
    )
    classification_state: str = Field(
        default=ClassificationState.CONFIRMED_ISSUE.value,
        description="CONFIRMED_ISSUE, POSSIBLE_ISSUE, or GENERAL_MESSAGE",
    )
    confidence: float = Field(
        default=0.95,
        description="Confidence score between 0.0 and 1.0",
    )
    general_summary: str = Field(
        default="",
        description="Brief summary of all issues reported in the client message",
    )
    issues: List[JiraTicketItem] = Field(
        default_factory=list,
        description="List of all distinct issues found in the message. If is_ticket_request is true, must contain an item for each distinct problem.",
    )



SYSTEM_PROMPT = """You are an expert Agile Scrum Master and Senior QA Technical Lead with computer vision expertise.
Your task is to analyze incoming messages and attachments (screenshots, logs, error reports) from a Microsoft Teams client support chat and convert ticket requests into professional, highly accurate Jira issue specifications.

Rules:
1. Classification:
   - CONFIRMED_ISSUE: If the text, attached screenshot, or log clearly demonstrates broken behavior, an error, bug, defect, or explicit task request.
   - POSSIBLE_ISSUE: If the user is reporting confusion, potential problem, or ambiguous request without clear reproduction.
   - GENERAL_MESSAGE: If the message is purely conversational greetings (e.g. "Good morning", "Thanks", "Can we hop on a call?") without any defect or task. For general messages, set is_ticket_request = false.

2. Multimodal Screenshot & Multi-Format Attachment Analysis:
   - IMAGES / SCREENSHOTS (.png, .jpg, .jpeg, .webp, .gif):
     - Inspect the UI for red error badges, toast notifications, broken alignment, form validation errors, or DevTools consoles.
     - Transcribe exact error message text into the "evidence" field.
     - Identify the affected module/screen (e.g. Checkout, Login, Dashboard, Billing).
   - EXCEL SPREADSHEETS (.xlsx, .xlsm, .xls):
     - Carefully inspect the tabular sheets, test case rows, bug lists, and column headers.
     - Extract reported defects, steps, expected/observed behaviors, and severities into individual Jira issue items.
   - WORD DOCUMENTS (.docx):
     - Read specifications, bug write-ups, requirements, and tables to capture the complete problem statement.
   - ZIP ARCHIVES (.zip):
     - Examine the archive tree, internal error logs, configuration files, and tracebacks to uncover root causes.
   - PDF DOCUMENTS (.pdf):
     - Analyze all text, sections, error reports, and specification pages.
   - LOG FILES & CODE (.log, .txt, .json, .csv, .py, .js, .sql, etc.):
     - Extract exact exception names, stack trace lines, HTTP status codes (500, 502, 504, 404), and error snippets into the "evidence" field.

3. Professional Ticket Perspective (CRITICAL ASTERISK FORBIDDEN RULE):
   - Provide a comprehensive, accurate defect or task specification from a senior QA / Scrum perspective.
   - NEVER use markdown bold asterisks (such as **Reported By**: or **Observed Behavior**:) in any output field.
   - Jira uses native Atlassian Document Format (ADF) UI components. Raw asterisks cause formatting glitches. Always output clean, readable plain text.
   - summary: Concise, professional title with module tag (e.g. "[Dashboard] Refresh button unresponsive when clicked").
   - observed_behavior: What went wrong or failed, clearly referencing UI elements and evidence.
   - expected_behavior: What the system should do under normal conditions.
   - steps_to_reproduce: Clear numbered steps to reproduce the issue.
   - evidence: Specific error codes, log snippets, or visual UI details observed.
   - acceptance_criteria: Definite conditions to verify resolution.

4. Multi-Issue Extraction (Single or Multiple Issues):
   - If the client's message reports multiple distinct bugs, errors, or requests (e.g. numbered items '1. ... 2. ...' or multiple bullet points), extract EACH distinct problem into the `issues` array as a separate issue object!
   - If only a single issue is reported, `issues` must contain exactly 1 issue object.
   - Populate `general_summary` with an overall summary of the message.
   - Set top-level `summary` to the first/primary issue.

5. Issue Type & Priority:
   - "Bug" for defects, broken features, errors, visual glitches.
   - "Task" for general work, configuration, credentials, access.
   - "Story" for new feature requests.
   - Priority: "Highest" or "High" if affecting the entire application, blocking checkouts/auth, or causing downtime. "Medium" for standard bugs. "Low" for minor cosmetic issues.

6. Developer Assignment Routing:
   - Direct Mentions: If message @mentions or specifies a developer name:
     - Musaib / Musain -> "Musaib Khan" (Frontend Lead)
     - Hemil -> "Hemil Ghori" (Backend Lead)
     - Nisit -> "Nisit Patel" (Database / Infra)
   - Module Specialty (if no mention):
     - Frontend, UI, CSS, Design, Responsive -> "Musaib Khan"
     - Backend, API, Server 500, Integrations -> "Hemil Ghori"
     - Database, SQL, Migration -> "Nisit Patel"
     - Otherwise: null (Awaiting PM Triage)
"""


class GeminiKeyPool:
    """Round-robin API Key pool with automatic failover on HTTP 429 / RESOURCE_EXHAUSTED."""

    def __init__(self):
        self._current_index = 0

    def get_keys(self) -> List[str]:
        keys = config.gemini.api_keys or ([config.gemini.api_key] if config.gemini.api_key else [])
        return [k.strip() for k in keys if k and k.strip()]

    def get_next_key(self) -> Optional[str]:
        keys = self.get_keys()
        if not keys:
            return None
        key = keys[self._current_index % len(keys)]
        self._current_index = (self._current_index + 1) % len(keys)
        return key


key_pool = GeminiKeyPool()


def optimize_image_for_lite_model(raw_bytes: bytes, mime_type: str, max_dimension: int = 1200) -> tuple[bytes, str]:
    """Optimize image dimensions and payload size to minimize token consumption on Flash-Lite models."""
    if not raw_bytes or len(raw_bytes) < 300_000:
        return (raw_bytes, mime_type)
    try:
        import io
        from PIL import Image

        image = Image.open(io.BytesIO(raw_bytes))
        width, height = image.size

        # If already within bounds, avoid recompressing unless over 700KB
        if width <= max_dimension and height <= max_dimension and len(raw_bytes) < 700_000:
            return (raw_bytes, mime_type)

        # Scale down proportionally if larger than max_dimension
        if width > max_dimension or height > max_dimension:
            scale = min(max_dimension / width, max_dimension / height)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        if mime_type == "image/png" and len(raw_bytes) < 1_200_000:
            image.save(out_buf, format="PNG", optimize=True)
            return (out_buf.getvalue(), "image/png")
        else:
            if image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(out_buf, format="JPEG", quality=80, optimize=True)
            return (out_buf.getvalue(), "image/jpeg")
    except Exception as opt_err:
        logger.debug(f"Image optimization skipped ({opt_err}), using original bytes")
        return (raw_bytes, mime_type)


def optimize_log_content_for_lite(decoded_text: str, max_chars: int = 5000) -> str:
    """Format logs and stack traces to prioritize error messages while staying within Flash-Lite token limits."""
    if len(decoded_text) <= max_chars:
        return decoded_text

    # Take the first 1,000 chars (headers/context) and last 3,800 chars (most recent stack trace)
    head = decoded_text[:1000]
    tail = decoded_text[-3800:]
    return f"{head}\n\n[... Snipped {len(decoded_text) - 4800} chars of intermediate log output ...]\n\n{tail}"


def extract_text_from_attachment(raw_bytes: bytes, name: str, content_type: str) -> Optional[str]:
    """Extract plain text / tabular / document content from any attachment format."""
    if not raw_bytes:
        return None

    try:
        from src.services.attachment_parser import extract_attachment_content
        parsed = extract_attachment_content(raw_bytes, name, content_type)
        extracted_text = parsed.get("text", "")
        if extracted_text and not parsed.get("is_image"):
            return optimize_log_content_for_lite(extracted_text)
    except Exception as exc:
        logger.debug(f"Attachment content extraction error for {name}: {exc}")

    return None

def _rule_based_fallback(
    text: str,
    sender_name: Optional[str] = None,
    sender_role: Optional[str] = None,
    has_attachments: bool = False,
    attachment_texts: Optional[List[str]] = None,
    attachment_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Smart offline rule-based parser when Gemini API keys are not configured or exhausted."""
    clean_text = text.strip()

    # Combine message text with any extracted attachment content and file names
    combined_body = clean_text
    if attachment_texts:
        combined_body += "\n" + "\n".join(attachment_texts)
    if attachment_names:
        combined_body += " " + " ".join(attachment_names)

    lower_text = combined_body.lower()

    # Detect if ticket request
    is_ticket = (
        any(kw in lower_text for kw in [
            "#issue", "#bug", "#task", "bug", "issue", "error", "fix", "fail",
            "broken", "crash", "not working", "404", "500", "exception", "timeout"
        ])
        or has_attachments
    )

    classification_state = ClassificationState.CONFIRMED_ISSUE.value if is_ticket else ClassificationState.GENERAL_MESSAGE.value

    # Determine type
    issue_type = "Bug" if any(w in lower_text for w in ["bug", "error", "broken", "failed", "crash", "500", "404", "exception", "timeout"]) else "Task"

    # Determine priority
    priority = "High" if any(w in lower_text for w in ["whole page", "entire", "urgent", "blocking", "critical", "crash", "down", "500"]) else "Medium"

    # Clean title
    title_text = re.sub(r'#\w+', '', clean_text).strip()
    lines = [line.strip() for line in title_text.splitlines() if line.strip()]
    if lines:
        raw_title = lines[0]
    elif attachment_names:
        raw_title = f"Issue reported with {attachment_names[0]}"
    elif has_attachments:
        raw_title = "Issue with attached screenshot/file"
    else:
        raw_title = "Teams Issue Report"

    raw_title = raw_title.replace('"', '').replace("'", "")
    if len(raw_title) > 65:
        raw_title = raw_title[:62] + "..."

    summary = f"[{issue_type}] {raw_title}"

    # Suggested assignee & module
    assignee = None
    assignee_rationale = None
    affected_module = "General"

    if any(k in lower_text for k in ["ui", "css", "button", "frontend", "screen", "page", "display"]):
        affected_module = "Frontend/UI"
        assignee = "Musaib Khan"
        assignee_rationale = "Frontend module specialist"
    elif any(k in lower_text for k in ["api", "server", "backend", "500", "endpoint", "database", "sql"]):
        affected_module = "Backend/API"
        assignee = "Hemil Ghori"
        assignee_rationale = "Backend module specialist"

    if "musaib" in lower_text or "musain" in lower_text:
        assignee = "Musaib Khan"
        assignee_rationale = "Directly mentioned in message"
    elif "hemil" in lower_text:
        assignee = "Hemil Ghori"
        assignee_rationale = "Directly mentioned in message"

    evidence = []
    if attachment_names:
        evidence.append(f"Attached file(s): {', '.join(attachment_names)}")
    elif has_attachments:
        evidence.append("Attached file / screenshot provided by reporter")

    for word in ["500", "404", "timeout", "exception", "error"]:
        if word in lower_text:
            evidence.append(f"Keyword match in message/attachment: '{word}'")

    if attachment_texts:
        for atxt in attachment_texts[:2]:
            snippet = atxt.replace("\n", " ").strip()
            if len(snippet) > 100:
                snippet = snippet[:97] + "..."
            evidence.append(f"Attachment excerpt: {snippet}")

    steps_to_reproduce = [
        f"Navigate to the {affected_module} section",
        f"Trigger operation related to: {raw_title}",
        "Observe the unexpected behavior or error condition",
    ]

    acceptance_criteria = [
        f"Operation completes successfully within {affected_module} without errors",
        "Clear visual confirmation or update is presented to the user",
    ]

    description = f"""Reported Issue
Reporter: {sender_name or 'Client'} ({sender_role or 'CLIENT'})

Raw Teams Message:
> {clean_text}

Details & Investigation:
- Context: Captured automatically from Microsoft Teams integration.
- Identified Type: {issue_type}
- Affected Module: {affected_module}
- Assigned Target: {assignee or 'Unassigned (Awaiting PM Triage)'}
- Evidence: {', '.join(evidence) if evidence else 'None observed in plain text'}
"""

    # Check if message contains multiple numbered or bulleted sub-issues
    numbered_items = re.findall(r'(?:^|\n)\s*(?:[0-9]+[.)]|[-*•])\s+([^\n]+(?:\n(?!\s*(?:[0-9]+[.)]|[-*•]|\Z))[^\n]+)*)', clean_text)
    if is_ticket and len(numbered_items) > 1:
        issues = []
        for idx, item_str in enumerate(numbered_items):
            item_clean = item_str.strip()
            item_lower = item_clean.lower()
            sub_type = "Bug" if any(w in item_lower for w in ["bug", "error", "broken", "failed", "crash", "500", "404", "exception", "timeout"]) else "Task"
            sub_priority = "High" if any(w in item_lower for w in ["whole page", "entire", "urgent", "blocking", "critical", "crash", "down", "500"]) else "Medium"
            sub_module = "General"
            sub_assignee = None
            sub_assignee_rationale = None

            if any(k in item_lower for k in ["ui", "css", "button", "frontend", "screen", "page", "display", "mobile", "navbar", "menu"]):
                sub_module = "Frontend/UI"
                sub_assignee = "Musaib Khan"
                sub_assignee_rationale = "Frontend module specialist"
            elif any(k in item_lower for k in ["api", "server", "backend", "500", "endpoint", "database", "sql"]):
                sub_module = "Backend/API"
                sub_assignee = "Hemil Ghori"
                sub_assignee_rationale = "Backend module specialist"

            if "musaib" in item_lower or "musain" in item_lower:
                sub_assignee = "Musaib Khan"
                sub_assignee_rationale = "Directly mentioned in message"
            elif "hemil" in item_lower:
                sub_assignee = "Hemil Ghori"
                sub_assignee_rationale = "Directly mentioned in message"

            sub_first_line = [l.strip() for l in item_clean.splitlines() if l.strip()][0]
            if len(sub_first_line) > 65:
                sub_first_line = sub_first_line[:62] + "..."
            sub_summary = f"[{sub_type}] {sub_first_line}"

            sub_evidence = []
            for word in ["500", "404", "timeout", "exception", "error"]:
                if word in item_lower:
                    sub_evidence.append(f"Keyword match: '{word}'")

            issues.append({
                "summary": sub_summary,
                "issue_type": sub_type,
                "priority": sub_priority,
                "priority_rationale": "Evaluated by heuristic keyword rules",
                "affected_module": sub_module,
                "observed_behavior": item_clean,
                "expected_behavior": "System operates normally without error",
                "steps_to_reproduce": [f"Navigate to {sub_module}", f"Trigger: {sub_first_line}"],
                "evidence": sub_evidence,
                "acceptance_criteria": [f"Defect resolved in {sub_module}"],
                "description": f"Sub-issue #{idx + 1} reported by {sender_name or 'Client'}:\n\n> {item_clean}",
                "suggested_assignee": sub_assignee,
                "assignee_rationale": sub_assignee_rationale,
                "reporter_name": sender_name,
                "reporter_role": sender_role,
                "labels": ["teams-automation", "client-reported", sub_type.lower()],
            })

        # Update primary fields from sub-issues: highest priority and bug type take precedence
        summary = issues[0]["summary"]
        issue_type = "Bug" if any(iss.get("issue_type") == "Bug" for iss in issues) else issues[0]["issue_type"]
        priority = "High" if any(iss.get("priority") == "High" for iss in issues) else issues[0]["priority"]
        affected_module = issues[0]["affected_module"]
        assignee = next((iss.get("suggested_assignee") for iss in issues if iss.get("suggested_assignee")), None)
        assignee_rationale = next((iss.get("assignee_rationale") for iss in issues if iss.get("assignee_rationale")), None)

        all_evidence = []
        for iss in issues:
            for ev in iss.get("evidence", []):
                if ev not in all_evidence:
                    all_evidence.append(ev)
        if all_evidence:
            evidence = all_evidence
    else:
        issues = [
            {
                "summary": summary,
                "issue_type": issue_type,
                "priority": priority,
                "priority_rationale": "Evaluated by heuristic keyword rules",
                "affected_module": affected_module,
                "observed_behavior": clean_text,
                "expected_behavior": "System operates normally without error",
                "steps_to_reproduce": steps_to_reproduce,
                "evidence": evidence,
                "acceptance_criteria": acceptance_criteria,
                "description": description.strip(),
                "suggested_assignee": assignee,
                "assignee_rationale": assignee_rationale,
                "reporter_name": sender_name,
                "reporter_role": sender_role,
                "labels": ["teams-automation", "client-reported", issue_type.lower()],
            }
        ]

    return {
        "is_ticket_request": is_ticket,
        "classification_state": classification_state,
        "general_summary": summary,
        "issues": issues,
        "summary": summary,
        "issue_type": issue_type,
        "priority": priority,
        "priority_rationale": "Evaluated by heuristic keyword rules",
        "affected_module": affected_module,
        "observed_behavior": clean_text,
        "expected_behavior": "System operates normally without error",
        "steps_to_reproduce": steps_to_reproduce,
        "evidence": evidence,
        "acceptance_criteria": acceptance_criteria,
        "description": description.strip(),
        "suggested_assignee": assignee,
        "assignee_rationale": assignee_rationale,
        "reporter_name": sender_name,
        "reporter_role": sender_role,
        "raw_message": clean_text,
        "labels": ["teams-automation", "client-reported", issue_type.lower()],
        "confidence": 0.85 if is_ticket else 0.95,
        "extractor": "heuristic_fallback",
    }


async def extract_jira_ticket(
    text: str,
    sender_name: Optional[str] = None,
    sender_role: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Extract structured Jira ticket fields from a Teams message & attachments using Gemini or heuristic fallback.

    Args:
        text: The message body text.
        sender_name: Sender's display name.
        sender_role: Identified sender role (CLIENT, PM, DEVELOPER).
        attachments: List of attachments, each a dict with 'name', 'content_type', and 'bytes'.
    """
    clean_text = (text or "").strip()
    att_list = attachments or []
    has_attachments = len(att_list) > 0

    if not clean_text and not has_attachments:
        return {
            "is_ticket_request": False,
            "classification_state": ClassificationState.GENERAL_MESSAGE.value,
            "summary": "Empty message",
            "issue_type": "Task",
            "priority": "Low",
            "description": "",
            "suggested_assignee": None,
            "labels": [],
            "confidence": 0.0,
            "extractor": "none",
        }

    # Pre-extract text from attachments if available
    attachment_texts: List[str] = []
    attachment_names: List[str] = []
    for att in att_list:
        raw_b = att.get("bytes")
        a_name = att.get("name", "attachment")
        attachment_names.append(a_name)
        c_type = att.get("content_type", "")
        if raw_b:
            txt = extract_text_from_attachment(raw_b, a_name, c_type)
            if txt:
                attachment_texts.append(f"[{a_name}]:\n{txt}")

    keys = key_pool.get_keys()
    if not keys:
        logger.info(
            "No GEMINI_API_KEYS configured in .env, using smart heuristic ticket extractor",
            extra={"event": "AI_EXTRACT_HEURISTIC"},
        )
        fallback = _rule_based_fallback(
            clean_text,
            sender_name=sender_name,
            sender_role=sender_role,
            has_attachments=has_attachments,
            attachment_texts=attachment_texts,
            attachment_names=attachment_names,
        )
        fallback["extractor"] = "heuristic_fallback (no API keys configured)"
        return fallback

    # Prepare multimodal contents
    prompt_text = f"""Sender: {sender_name or 'Client'} (Role: {sender_role or 'CLIENT'})
Message Content:
\"\"\"
{clean_text or '[No text provided, see attachments]'}
\"\"\"
"""
    if has_attachments:
        prompt_text += f"\nNote: The user attached {len(att_list)} file(s)/screenshot(s): {', '.join(attachment_names)}. Analyze both the text and visual/file attachments carefully."

    # Try each key in the pool with automatic failover
    from google import genai
    from google.genai import types
    import mimetypes

    last_error = None
    for attempt in range(len(keys)):
        api_key = key_pool.get_next_key()
        if not api_key:
            break

        try:
            client = genai.Client(api_key=api_key)

            # Build contents list (text prompt + image/file parts)
            content_parts = [prompt_text]

            for att in att_list:
                raw_bytes = att.get("bytes")
                content_type = att.get("content_type", "")
                name = att.get("name", "attachment")

                if not raw_bytes:
                    continue

                from src.services.attachment_parser import extract_attachment_content
                parsed = extract_attachment_content(raw_bytes, name, content_type)
                fmt = parsed.get("format")

                if fmt == "image":
                    try:
                        opt_bytes, opt_mime = optimize_image_for_lite_model(raw_bytes, parsed.get("mime_type", "image/png"))
                        content_parts.append(
                            types.Part.from_bytes(data=opt_bytes, mime_type=opt_mime)
                        )
                    except Exception as img_err:
                        logger.warning(f"Could not convert image attachment '{name}': {img_err}")
                elif fmt == "pdf":
                    # 1. Native multimodal PDF part for Gemini document vision
                    try:
                        content_parts.append(
                            types.Part.from_bytes(data=raw_bytes, mime_type="application/pdf")
                        )
                    except Exception as pdf_err:
                        logger.warning(f"Could not convert PDF attachment '{name}' to multimodal part: {pdf_err}")
                    # 2. Extracted text stream for grounded inspection
                    pdf_text = parsed.get("text", "")
                    if pdf_text:
                        summary_str = parsed.get("summary", "Document")
                        content_parts.append(f"\n--- Attached Document: {name} ({summary_str}) ---\n{pdf_text}\n--- End of Document ---\n")
                else:
                    # Word, Excel, ZIP, Text, Logs, Code, Configs
                    txt = parsed.get("text", "")
                    if txt:
                        summary_str = parsed.get("summary", "File")
                        content_parts.append(f"\n--- Attached File: {name} ({summary_str}) ---\n{txt}\n--- End of File ---\n")

            def _call_gemini():
                return client.models.generate_content(
                    model=config.gemini.model,
                    contents=content_parts,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=JiraTicketDraft,
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )

            response = await asyncio.to_thread(_call_gemini)

            parsed = json.loads(response.text)
            parsed["reporter_name"] = sender_name
            parsed["reporter_role"] = sender_role
            parsed["raw_message"] = clean_text

            raw_issues = parsed.get("issues") or []
            if not raw_issues and parsed.get("summary"):
                raw_issues = [{
                    "summary": parsed.get("summary"),
                    "issue_type": parsed.get("issue_type", "Bug"),
                    "priority": parsed.get("priority", "Medium"),
                    "affected_module": parsed.get("affected_module", "General"),
                    "observed_behavior": parsed.get("observed_behavior"),
                    "expected_behavior": parsed.get("expected_behavior"),
                    "steps_to_reproduce": parsed.get("steps_to_reproduce", []),
                    "evidence": parsed.get("evidence", []),
                    "acceptance_criteria": parsed.get("acceptance_criteria", []),
                    "description": parsed.get("description", ""),
                    "suggested_assignee": parsed.get("suggested_assignee"),
                    "labels": parsed.get("labels", ["teams-automation"]),
                }]
            elif raw_issues:
                primary = raw_issues[0]
                parsed["summary"] = primary.get("summary", "Issue Report")
                parsed["issue_type"] = primary.get("issue_type", "Bug")
                parsed["priority"] = primary.get("priority", "Medium")
                parsed["affected_module"] = primary.get("affected_module", "General")
                parsed["observed_behavior"] = primary.get("observed_behavior")
                parsed["expected_behavior"] = primary.get("expected_behavior")
                parsed["steps_to_reproduce"] = primary.get("steps_to_reproduce", [])
                parsed["evidence"] = primary.get("evidence", [])
                parsed["acceptance_criteria"] = primary.get("acceptance_criteria", [])
                parsed["description"] = primary.get("description", "")
                parsed["suggested_assignee"] = primary.get("suggested_assignee")
                parsed["assignee_rationale"] = primary.get("assignee_rationale")
            else:
                parsed.setdefault("summary", parsed.get("general_summary") or "General message")
                parsed.setdefault("issue_type", "Task")
                parsed.setdefault("priority", "Low")
                parsed.setdefault("affected_module", "General")
                parsed.setdefault("observed_behavior", None)
                parsed.setdefault("expected_behavior", None)
                parsed.setdefault("steps_to_reproduce", [])
                parsed.setdefault("evidence", [])
                parsed.setdefault("acceptance_criteria", [])
                parsed.setdefault("description", "")
                parsed.setdefault("suggested_assignee", None)
                parsed.setdefault("assignee_rationale", None)

            if raw_issues and not parsed.get("assignee_rationale"):
                parsed["assignee_rationale"] = raw_issues[0].get("assignee_rationale")

            for iss in raw_issues:
                iss["reporter_name"] = sender_name
                iss["reporter_role"] = sender_role
                if "labels" not in iss or not isinstance(iss["labels"], list):
                    iss["labels"] = ["teams-automation"]
                elif "teams-automation" not in iss["labels"]:
                    iss["labels"].append("teams-automation")

            parsed["issues"] = raw_issues

            if "labels" in parsed and isinstance(parsed["labels"], list):
                if "teams-automation" not in parsed["labels"]:
                    parsed["labels"].append("teams-automation")
            else:
                parsed["labels"] = ["teams-automation"]

            parsed["extractor"] = f"gemini ({config.gemini.model})"
            logger.info(
                f"AI ticket successfully extracted with {config.gemini.model} ({len(raw_issues)} issue(s) identified)",
                extra={"event": "AI_TICKET_EXTRACTED", "summary": parsed.get("summary"), "issuesCount": len(raw_issues)},
            )
            return parsed

        except Exception as err:
            err_str = str(err)
            last_error = err
            # If 429 or quota limit, log and rotate to next key with brief backoff
            is_rate_limit = any(term in err_str.lower() for term in ["429", "resource_exhausted", "quota", "rate limit"])
            if is_rate_limit:
                logger.warning(
                    f"Gemini {config.gemini.model} key exhausted ({err_str[:120]}), failing over to next key in pool...",
                    extra={"event": "AI_KEY_FAILOVER", "attempt": attempt + 1, "model": config.gemini.model},
                )
                await asyncio.sleep(0.3)
            else:
                logger.warning(
                    f"Gemini API call failed with key ({err_str[:120]}), rotating...",
                    extra={"event": "AI_EXTRACT_ERROR", "error": err_str[:200]},
                )

    logger.warning(
        f"All Gemini API keys exhausted or failed ({last_error}), falling back to heuristic extractor",
        extra={"event": "AI_ALL_KEYS_EXHAUSTED", "error": str(last_error)},
    )
    fallback = _rule_based_fallback(
        clean_text,
        sender_name=sender_name,
        sender_role=sender_role,
        has_attachments=has_attachments,
        attachment_texts=attachment_texts,
        attachment_names=attachment_names,
    )
    fallback["extractor"] = "heuristic_fallback (API quota exhausted / failover)"
    return fallback
