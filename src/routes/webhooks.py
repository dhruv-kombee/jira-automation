from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Request, Query, status
from fastapi.responses import PlainTextResponse, JSONResponse
from src.logger import logger
from src.services.message_service import process_teams_message

router = APIRouter(prefix="/webhooks/teams")


async def handle_notifications_background(notifications: List[Dict[str, Any]]):
    """Process incoming Graph notifications in the background."""
    seen_resources = set()
    for notification in notifications:
        try:
            # Skip lifecycle events (e.g., subscriptionRemoved, missed)
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

            # Deduplicate multiple identical notifications for the same resource within the batch
            resource = notification.get("resource")
            if resource:
                if resource in seen_resources:
                    logger.debug(f"Ignoring duplicate notification for {resource} in same batch")
                    continue
                seen_resources.add(resource)

            # Log clientState if present
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
