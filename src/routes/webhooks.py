from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Request, Query, status
from fastapi.responses import PlainTextResponse, JSONResponse
from src.config import config
from src.logger import logger
from src.services.message_service import process_teams_message

router = APIRouter(prefix="/webhooks/teams")


import collections
import time

# Rolling window for cross-batch notification deduplication
_RECENT_RESOURCES = collections.OrderedDict()  # dedup_key -> timestamp
_MAX_RECENT_RESOURCES = 500
_RESOURCE_DEDUP_WINDOW = 4.0  # seconds


async def handle_notifications_background(notifications: List[Dict[str, Any]]):
    """Process incoming Graph notifications in the background."""
    now = time.time()
    # Prune old cache entries
    while _RECENT_RESOURCES and (now - next(iter(_RECENT_RESOURCES.values()))) > 60.0:
        _RECENT_RESOURCES.popitem(last=False)

    for notification in notifications:
        try:
            # 1. Skip lifecycle events (e.g., subscriptionRemoved, missed)
            lifecycle_event = notification.get("lifecycleEvent")
            if lifecycle_event:
                logger.info(
                    "Lifecycle event received",
                    extra={
                        "event": "GRAPH_NOTIFICATION_RECEIVED",
                        "lifecycleEvent": lifecycle_event,
                        "subscriptionId": notification.get("subscriptionId"),
                    },
                )
                continue

            # 2. Tenant boundary security validation
            tenant_id = notification.get("tenantId")
            if tenant_id and config.microsoft.tenant_id:
                if tenant_id.lower().strip() != config.microsoft.tenant_id.lower().strip():
                    logger.warning(
                        f"Untrusted tenant notification rejected: {tenant_id}",
                        extra={"event": "UNTRUSTED_TENANT", "tenantId": tenant_id},
                    )
                    continue

            # 3. Deduplicate rapid duplicate notifications (both intra-batch and cross-retry)
            resource = notification.get("resource")
            change_type = (notification.get("changeType") or "").lower().strip()
            if resource:
                dedup_key = f"{resource}:{change_type}"
                curr_time = time.time()
                last_seen = _RECENT_RESOURCES.get(dedup_key)
                window = 1.5 if change_type != "created" else _RESOURCE_DEDUP_WINDOW
                if last_seen and (curr_time - last_seen) < window:
                    logger.debug(f"Ignoring duplicate notification for {dedup_key} within {window}s window")
                    continue
                _RECENT_RESOURCES[dedup_key] = curr_time
                if len(_RECENT_RESOURCES) > _MAX_RECENT_RESOURCES:
                    _RECENT_RESOURCES.popitem(last=False)

            # 4. Log clientState
            if notification.get("clientState"):
                logger.debug(
                    "Client state present in notification",
                    extra={"subscriptionId": notification.get("subscriptionId")},
                )

            await process_teams_message(notification)
        except Exception as err:
            logger.error(
                "Failed to process notification",
                extra={
                    "event": "GRAPH_API_ERROR",
                    "error": str(err),
                    "resource": notification.get("resource"),
                    "subscriptionId": notification.get("subscriptionId"),
                },
            )


@router.get("", response_class=PlainTextResponse)
@router.get("/", response_class=PlainTextResponse)
async def validate_subscription_get(
    validationToken: Optional[str] = Query(None, alias="validationToken")
):
    """GET /webhooks/teams — Subscription validation (echoes validationToken)."""
    if validationToken:
        logger.info(
            "Graph subscription validation (GET)",
            extra={"event": "GRAPH_NOTIFICATION_RECEIVED", "type": "validation"},
        )
        return PlainTextResponse(content=validationToken, status_code=status.HTTP_200_OK)

    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "Missing validationToken"})


@router.post("")
@router.post("/")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    validationToken: Optional[str] = Query(None, alias="validationToken"),
):
    """POST /webhooks/teams — Notification receiver and validation."""
    # 1. Subscription Validation
    if validationToken:
        logger.info(
            "Graph subscription validation (POST)",
            extra={"event": "GRAPH_NOTIFICATION_RECEIVED", "type": "validation"},
        )
        return PlainTextResponse(content=validationToken, status_code=status.HTTP_200_OK)

    # 2. Change Notification Processing
    try:
        body = await request.json()
    except Exception:
        body = None

    if not body or not isinstance(body.get("value"), list):
        # Check if this is an Adaptive Card Action.Submit or bot activity from Teams
        if body and (
            body.get("action")
            or body.get("type") in ("message", "invoke")
            or isinstance(body.get("value"), dict)
            or body.get("selected_assignee")
            or (body.get("text") and ("approve_ticket" in str(body.get("text")) or "decline_ticket" in str(body.get("text"))))
        ):
            return await handle_teams_card_action(body)

        logger.warning(
            "Invalid Graph notification payload",
            extra={"event": "GRAPH_NOTIFICATION_RECEIVED", "type": "invalid"},
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid notification payload"},
        )

    notifications = body["value"]
    logger.info(
        f"Received {len(notifications)} Graph notification(s)",
        extra={
            "event": "GRAPH_NOTIFICATION_RECEIVED",
            "type": "notification",
            "count": len(notifications),
        },
    )

    # Microsoft Graph expects an immediate 202 Accepted response (< 3 seconds)
    background_tasks.add_task(handle_notifications_background, notifications)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "accepted"})


async def handle_teams_card_action(payload: Dict[str, Any]) -> JSONResponse:
    """Handle native in-Teams Adaptive Card Submit actions (Method 1: 100% in-Teams)."""
    from src.services.sender_service import is_user_authorized_approver
    from src.services.member_sync_service import get_member_by_id_or_name
    from src.services.message_service import (
        execute_jira_ticket_creation,
        execute_jira_ticket_decline,
    )

    # 1. Extract action, message_id, issue_idx, selected_assignee
    action_data = payload
    if isinstance(payload.get("value"), dict):
        action_data = payload["value"]
    elif isinstance(payload.get("data"), dict):
        action_data = payload["data"]

    action = (action_data.get("action") or payload.get("action") or "").strip().lower()
    message_id = action_data.get("message_id") or payload.get("message_id")
    issue_idx = action_data.get("issue_idx") if action_data.get("issue_idx") is not None else payload.get("issue_idx")
    try:
        if issue_idx is not None:
            issue_idx = int(issue_idx)
    except (ValueError, TypeError):
        issue_idx = None

    selected_assignee = (
        action_data.get("selected_assignee")
        or (action_data.get(f"selected_assignee_{issue_idx}") if issue_idx is not None else None)
        or payload.get("selected_assignee")
        or (payload.get(f"selected_assignee_{issue_idx}") if issue_idx is not None else None)
    )

    # Fallback parsing from text command (e.g. "approve_ticket msg-123 0")
    text_cmd = (payload.get("text") or "").strip()
    if text_cmd.startswith("approve_ticket"):
        parts = text_cmd.split()
        action = action or "approve"
        if len(parts) > 1 and not message_id:
            message_id = parts[1]
        if len(parts) > 2 and issue_idx is None:
            try:
                issue_idx = int(parts[2])
            except ValueError:
                pass
    elif text_cmd.startswith("decline_ticket"):
        parts = text_cmd.split()
        action = action or "decline"
        if len(parts) > 1 and not message_id:
            message_id = parts[1]
        if len(parts) > 2 and issue_idx is None:
            try:
                issue_idx = int(parts[2])
            except ValueError:
                pass

    if not message_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Missing message_id in card action payload"},
        )

    # 2. Extract submitter identity
    user_obj = payload.get("user") or payload.get("from") or {}
    if isinstance(payload.get("value"), dict):
        val_obj = payload["value"]
        if "user" in val_obj and isinstance(val_obj["user"], dict):
            user_obj = val_obj["user"]
        elif "from" in val_obj and isinstance(val_obj["from"], dict):
            user_obj = val_obj["from"]

    submitter_id = (
        user_obj.get("id")
        or user_obj.get("aadObjectId")
        or user_obj.get("userId")
        or payload.get("reviewer")
        or payload.get("approver_id")
        or payload.get("approver")
    )
    submitter_name = (
        user_obj.get("name")
        or user_obj.get("displayName")
        or payload.get("reviewer")
        or payload.get("approver_id")
        or payload.get("approver")
    )

    # 3. RBAC Enforcement
    is_auth = is_user_authorized_approver(user_id=submitter_id, display_name=submitter_name)
    member_record = get_member_by_id_or_name(user_id=submitter_id, display_name=submitter_name)
    user_role = (member_record.get("role") if member_record else "DEVELOPER") or "DEVELOPER"
    clean_name = (member_record.get("display_name") if member_record else submitter_name) or "User"

    if not is_auth:
        logger.warning(
            f"⛔ Unauthorized in-card action attempt for message {message_id} by '{clean_name}' (Role: {user_role})",
            extra={
                "event": "UNAUTHORIZED_TEAMS_CARD_ACTION",
                "user": clean_name,
                "role": user_role,
                "action": action,
                "messageId": message_id,
            },
        )
        forbidden_card = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "Container",
                    "style": "attention",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "⛔ **Access Denied: Action Strictly Not Permitted**",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": "Attention",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"@{clean_name}, you have role **{user_role}**.\n\nYou are strictly not permitted to approve or reject tickets. Only **Team Lead (TL)**, **Project Manager (PM)**, or **Higher Management (HM)** can approve or decline Jira tickets.\n\n*Please ask your TL, PM, or HM to confirm.*",
                            "wrap": True,
                            "spacing": "Medium",
                        },
                    ],
                }
            ],
        }
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": f"Authority Check Failed: You have role '{user_role}'. Only Team Lead (TL), Project Manager (PM), or Higher Management (HM) can approve or reject tickets.",
                "card": forbidden_card,
            },
        )

    # 4. Authorized Action Execution
    approver_full_name = f"{user_role} {clean_name}"
    if action == "approve":
        res = await execute_jira_ticket_creation(
            message_id=message_id,
            approver_name=approver_full_name,
            issue_idx=issue_idx,
            assignee_override=selected_assignee,
        )
        if res.get("success"):
            k = res.get("key") or "SCRUM-TICKET"
            u = res.get("url") or "#"
            success_card = {
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "Container",
                        "style": "good",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": f"🎉 **Jira Ticket Created: [{k}]({u})**",
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": "Good",
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Approved By:", "value": approver_full_name},
                                    {"title": "Assigned To:", "value": selected_assignee or "Assigned Dev"},
                                    {"title": "Ticket Key:", "value": k},
                                ],
                            },
                        ],
                    }
                ],
            }
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "key": k, "url": u, "card": success_card},
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "error": res.get("error", "Failed to create ticket")},
            )

    elif action in ("decline", "reject"):
        reason = action_data.get("reason") or "Declined by Management in Teams"
        res = await execute_jira_ticket_decline(
            message_id=message_id,
            approver_name=approver_full_name,
            reason=reason,
            issue_idx=issue_idx,
        )
        if res.get("success"):
            declined_card = {
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {
                        "type": "Container",
                        "style": "warning",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": "❌ **Ticket Creation Rejected**",
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": "Warning",
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Rejected By:", "value": approver_full_name},
                                    {"title": "Reason:", "value": reason},
                                    {"title": "Status:", "value": "No tickets created in Jira"},
                                ],
                            },
                        ],
                    }
                ],
            }
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"success": True, "card": declined_card},
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"success": False, "error": res.get("error", "Failed to decline ticket")},
            )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": f"Unknown action: {action}"},
    )


@router.post("/actions")
@router.post("/card-action")
async def receive_teams_action(request: Request):
    """POST /webhooks/teams/actions — Direct receiver for Teams Adaptive Card Submit actions."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await handle_teams_card_action(body)
