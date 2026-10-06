import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import httpx
import msal

from src.config import config
from src.logger import logger

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

_msal_app: Optional[msal.ConfidentialClientApplication] = None


def get_msal_app() -> msal.ConfidentialClientApplication:
    """Initialize or get the MSAL ConfidentialClientApplication."""
    global _msal_app
    if _msal_app is not None:
        return _msal_app

    tenant_id = config.microsoft.tenant_id
    client_id = config.microsoft.client_id
    client_secret = config.microsoft.client_secret

    if not tenant_id or not client_id or not client_secret:
        err = RuntimeError("Microsoft Graph credentials are not configured. Check .env file.")
        logger.error(str(err), extra={"event": "AUTHENTICATION_ERROR"})
        raise err

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    _msal_app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )
    logger.info("Microsoft Graph MSAL client initialized", extra={"event": "GRAPH_CLIENT_INIT"})
    return _msal_app


def get_access_token() -> str:
    """Acquire a Microsoft Graph access token using client credentials flow with caching."""
    app = get_msal_app()

    # Try silent token acquisition from cache first
    result = app.acquire_token_silent(GRAPH_SCOPES, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPES)

    if "access_token" in result:
        return result["access_token"]

    error_description = result.get("error_description", result.get("error", "Unknown MSAL error"))
    logger.error(
        f"Failed to acquire Microsoft Graph token: {error_description}",
        extra={"event": "AUTHENTICATION_ERROR", "details": result},
    )
    raise RuntimeError(f"MSAL authentication failed: {error_description}")


def _handle_graph_error(response: httpx.Response, context: Optional[Dict[str, Any]] = None):
    """Handle and log Microsoft Graph API HTTP errors with structured context."""
    context = context or {}
    status_code = response.status_code
    error_text = response.text

    try:
        error_json = response.json()
        error_desc = error_json.get("error", {}).get("message", error_text)
    except Exception:
        error_desc = error_text

    context_info = {
        "statusCode": status_code,
        "graphError": error_desc,
        **context,
    }

    if status_code in (401, 403):
        logger.error(f"Graph authentication/permission failure: {error_desc}", extra={"event": "AUTHENTICATION_ERROR", **context_info})
    elif status_code == 404:
        logger.error(f"Graph resource not found: {error_desc}", extra={"event": "GRAPH_API_ERROR", **context_info})
    elif status_code == 429:
        retry_after = response.headers.get("retry-after", "unknown")
        logger.warning(f"Graph API throttled: {error_desc}", extra={"event": "GRAPH_API_ERROR", "retryAfter": retry_after, **context_info})
    else:
        logger.error(f"Graph API error ({status_code}): {error_desc}", extra={"event": "GRAPH_API_ERROR", **context_info})

    response.raise_for_status()


# --- Async Methods for Webhook and App ---

async def get_channel_message(team_id: str, channel_id: str, message_id: str) -> Dict[str, Any]:
    """Retrieve a specific Teams channel message asynchronously."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{message_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response, {"teamId": team_id, "channelId": channel_id, "messageId": message_id})

        logger.info(
            "Teams message retrieved from Graph",
            extra={
                "event": "TEAMS_MESSAGE_RETRIEVED",
                "messageId": message_id,
                "teamId": team_id,
                "channelId": channel_id,
            },
        )
        return response.json()


async def get_channel_message_reply(
    team_id: str, channel_id: str, parent_message_id: str, reply_message_id: str
) -> Dict[str, Any]:
    """Retrieve a reply message within a channel thread asynchronously."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{parent_message_id}/replies/{reply_message_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(
                response,
                {
                    "teamId": team_id,
                    "channelId": channel_id,
                    "parentMessageId": parent_message_id,
                    "replyMessageId": reply_message_id,
                },
            )

        logger.info(
            "Teams reply message retrieved from Graph",
            extra={
                "event": "TEAMS_MESSAGE_RETRIEVED",
                "messageId": reply_message_id,
                "parentMessageId": parent_message_id,
                "teamId": team_id,
                "channelId": channel_id,
            },
        )
        return response.json()


async def get_chat_message(chat_id: str, message_id: str) -> Dict[str, Any]:
    """Retrieve a specific Teams group/1:1 chat message asynchronously."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/chats/{chat_id}/messages/{message_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response, {"chatId": chat_id, "messageId": message_id})

        logger.info(
            "Teams chat message retrieved from Graph",
            extra={
                "event": "TEAMS_MESSAGE_RETRIEVED",
                "messageId": message_id,
                "chatId": chat_id,
            },
        )
        return response.json()


async def send_chat_message(
    chat_id: str,
    content: str,
    content_type: str = "html",
    mentions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Send a message/notification to a Teams group chat with optional @mentions."""
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{GRAPH_BASE_URL}/chats/{chat_id}/messages"
        payload: Dict[str, Any] = {
            "body": {
                "contentType": content_type,
                "content": content,
            }
        }
        if mentions:
            payload["mentions"] = mentions

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                logger.info("Teams confirmation message sent", extra={"event": "TEAMS_REPLY_SENT", "chatId": chat_id})
                return response.json()
            logger.warning(
                f"Teams send message warning ({response.status_code}): {response.text[:200]}",
                extra={"event": "TEAMS_SEND_WARNING", "status": response.status_code},
            )
            return None
    except Exception as err:
        logger.warning(f"Failed to post confirmation to Teams chat: {err}", extra={"event": "TEAMS_SEND_ERROR", "error": str(err)})
        return None


async def send_channel_reply(
    team_id: str,
    channel_id: str,
    parent_message_id: str,
    content: str,
    content_type: str = "html",
    mentions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Send a reply to a Teams channel thread with optional @mentions."""
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages/{parent_message_id}/replies"
        payload: Dict[str, Any] = {
            "body": {
                "contentType": content_type,
                "content": content,
            }
        }
        if mentions:
            payload["mentions"] = mentions

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                logger.info("Teams channel reply sent", extra={"event": "TEAMS_REPLY_SENT", "messageId": parent_message_id})
                return response.json()
            logger.warning(
                f"Teams send reply warning ({response.status_code}): {response.text[:200]}",
                extra={"event": "TEAMS_SEND_WARNING", "status": response.status_code},
            )
            return None
    except Exception as err:
        logger.warning(f"Failed to post confirmation to Teams channel: {err}", extra={"event": "TEAMS_SEND_ERROR", "error": str(err)})
        return None


async def send_chat_reply_with_quote(
    chat_id: str,
    message_id: str,
    content: str,
    content_type: str = "html",
    mentions: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Reply to a specific Teams chat message quoting the original message."""
    try:
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{GRAPH_BASE_URL}/chats/{chat_id}/messages/replyWithQuote"
        reply_msg: Dict[str, Any] = {
            "body": {
                "contentType": content_type,
                "content": content,
            }
        }
        if mentions:
            reply_msg["mentions"] = mentions

        payload = {
            "messageIds": [message_id],
            "replyMessage": reply_msg,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                logger.info("Teams chat replyWithQuote sent", extra={"event": "TEAMS_QUOTE_REPLY_SENT", "messageId": message_id})
                return response.json()
            logger.debug(
                f"Teams replyWithQuote warning ({response.status_code}): {response.text[:200]}",
                extra={"event": "TEAMS_QUOTE_REPLY_WARN", "status": response.status_code},
            )
            return None
    except Exception as err:
        logger.debug(f"Failed to reply with quote in chat: {err}")
        return None


# --- Subscription Management Methods (Sync / CLI compatible) ---

def generate_client_state() -> str:
    """Generate a random client state string for subscription validation."""
    return str(uuid.uuid4())


def create_subscription(
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    notification_url: str = "",
    expiration_minutes: int = 60,
) -> Dict[str, Any]:
    """Create a Microsoft Graph subscription for channel or chat messages."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{GRAPH_BASE_URL}/subscriptions"

    expiration_date_time = (
        datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes)
    ).isoformat().replace("+00:00", "Z")

    if chat_id:
        clean_chat = chat_id.strip()
        resource_path = f"chats/{clean_chat}/messages"
    elif team_id and channel_id:
        resource_path = f"teams/{team_id.strip()}/channels/{channel_id.strip()}/messages"
    else:
        raise ValueError("Either chat_id or both team_id and channel_id must be provided")

    payload = {
        "changeType": "created,updated",
        "notificationUrl": notification_url,
        "resource": resource_path,
        "expirationDateTime": expiration_date_time,
        "clientState": generate_client_state(),
    }

    with httpx.Client(timeout=15.0) as client:
        response = client.post(url, headers=headers, json=payload)
        if response.is_error:
            _handle_graph_error(response, {"resource": payload["resource"]})

        result = response.json()
        logger.info(
            "Graph subscription created",
            extra={
                "event": "SUBSCRIPTION_CREATED",
                "subscriptionId": result.get("id"),
                "resource": result.get("resource"),
                "expirationDateTime": result.get("expirationDateTime"),
            },
        )
        return result


def renew_subscription(subscription_id: str, expiration_minutes: int = 58) -> Dict[str, Any]:
    """Renew an existing subscription (defaults to 58 minutes to prevent Graph clock-skew rejections)."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}"

    # Graph chat subscriptions have 60m hard limit. Clamp to 58 to ensure no clock drift errors.
    effective_minutes = min(expiration_minutes, 58)
    expiration_date_time = (
        datetime.now(timezone.utc) + timedelta(minutes=effective_minutes)
    ).isoformat().replace("+00:00", "Z")

    payload = {"expirationDateTime": expiration_date_time}

    with httpx.Client(timeout=15.0) as client:
        response = client.patch(url, headers=headers, json=payload)
        if response.is_error:
            _handle_graph_error(response, {"subscriptionId": subscription_id})

        result = response.json()
        logger.info(
            "Graph subscription renewed",
            extra={
                "event": "SUBSCRIPTION_RENEWED",
                "subscriptionId": subscription_id,
                "expirationDateTime": result.get("expirationDateTime"),
            },
        )
        return result


async def async_renew_subscription(subscription_id: str, expiration_minutes: int = 58) -> Dict[str, Any]:
    """Asynchronously renew an existing subscription without blocking the FastAPI event loop."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}"

    effective_minutes = min(expiration_minutes, 58)
    expiration_date_time = (
        datetime.now(timezone.utc) + timedelta(minutes=effective_minutes)
    ).isoformat().replace("+00:00", "Z")

    payload = {"expirationDateTime": expiration_date_time}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.patch(url, headers=headers, json=payload)
        if response.is_error:
            _handle_graph_error(response, {"subscriptionId": subscription_id})

        result = response.json()
        logger.info(
            "Graph subscription renewed (async)",
            extra={
                "event": "SUBSCRIPTION_RENEWED",
                "subscriptionId": subscription_id,
                "expirationDateTime": result.get("expirationDateTime"),
            },
        )
        return result


async def async_create_subscription(
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    notification_url: str = "",
    expiration_minutes: int = 58,
) -> Dict[str, Any]:
    """Asynchronously create a Microsoft Graph subscription without blocking the FastAPI event loop."""
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{GRAPH_BASE_URL}/subscriptions"

    effective_minutes = min(expiration_minutes, 58)
    expiration_date_time = (
        datetime.now(timezone.utc) + timedelta(minutes=effective_minutes)
    ).isoformat().replace("+00:00", "Z")

    if chat_id:
        clean_chat = chat_id.strip()
        resource_path = f"chats/{clean_chat}/messages"
    elif team_id and channel_id:
        resource_path = f"teams/{team_id.strip()}/channels/{channel_id.strip()}/messages"
    else:
        raise ValueError("Either chat_id or both team_id and channel_id must be provided")

    payload = {
        "changeType": "created,updated",
        "notificationUrl": notification_url,
        "resource": resource_path,
        "expirationDateTime": expiration_date_time,
        "clientState": generate_client_state(),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.is_error:
            _handle_graph_error(response, {"resource": payload["resource"]})

        result = response.json()
        logger.info(
            "Graph subscription created (async)",
            extra={
                "event": "SUBSCRIPTION_CREATED",
                "subscriptionId": result.get("id"),
                "resource": result.get("resource"),
                "expirationDateTime": result.get("expirationDateTime"),
            },
        )
        return result



async def list_chat_messages(chat_id: str, top: int = 15) -> List[Dict[str, Any]]:
    """Retrieve recent messages from a Teams group/1:1 chat."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/chats/{chat_id}/messages?$top={top}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response, {"chatId": chat_id})
        data = response.json()
        return data.get("value", [])


async def list_channel_messages(team_id: str, channel_id: str, top: int = 15) -> List[Dict[str, Any]]:
    """Retrieve recent messages from a Teams channel."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages?$top={top}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response, {"teamId": team_id, "channelId": channel_id})
        data = response.json()
        return data.get("value", [])


def list_subscriptions() -> List[Dict[str, Any]]:
    """List all active subscriptions."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/subscriptions"

    with httpx.Client(timeout=15.0) as client:
        response = client.get(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response)

        data = response.json()
        return data.get("value", [])


def delete_subscription(subscription_id: str) -> None:
    """Delete a subscription."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}"

    with httpx.Client(timeout=15.0) as client:
        response = client.delete(url, headers=headers)
        if response.is_error:
            _handle_graph_error(response, {"subscriptionId": subscription_id})

        logger.info(
            "Graph subscription deleted",
            extra={
                "event": "SUBSCRIPTION_DELETED",
                "subscriptionId": subscription_id,
            },
        )


async def download_hosted_content(content_url: str) -> Optional[tuple[bytes, str]]:
    """Download binary content (such as inline pasted screenshots) from Microsoft Graph.

    Handles Graph authenticated endpoints and pre-signed CDN/blob redirect fallbacks.
    Returns (bytes, content_type) tuple or None if failed.
    """
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            res = await client.get(content_url, headers=headers)
            if res.status_code in (400, 401, 403):
                # If pre-signed Azure blob or SharePoint CDN, retry without Graph Bearer auth
                res = await client.get(content_url)

            if res.status_code == 200:
                content_type = res.headers.get("content-type", "image/png").split(";")[0].strip()
                logger.info(f"Downloaded inline hosted content ({len(res.content)} bytes, {content_type})")
                return (res.content, content_type)
            else:
                logger.warning(f"Failed to download hosted content from {content_url}: HTTP {res.status_code}")
                return None
    except Exception as err:
        logger.warning(f"Error downloading hosted content: {err}")
        return None


async def download_attachment_bytes(content_url: str) -> Optional[tuple[bytes, str]]:
    """Download attachment file bytes (logs, images, PDFs) from Graph or content URL.

    Handles SharePoint, OneDrive, and Teams CDN redirect fallbacks.
    Returns (bytes, content_type) tuple or None if failed.
    """
    import mimetypes
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # First try with auth header (Graph endpoint)
            res = await client.get(content_url, headers=headers)
            if res.status_code in (400, 401, 403):
                # If SharePoint/OneDrive public link or SAS blob URL, retry without Graph Bearer auth
                res = await client.get(content_url)

            if res.status_code == 200:
                content_type = res.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                # If content-type is generic, infer from URL path if possible
                if content_type in ("application/octet-stream", "text/plain") and "." in content_url:
                    guessed_mime, _ = mimetypes.guess_type(content_url.split("?")[0])
                    if guessed_mime:
                        content_type = guessed_mime
                logger.info(f"Downloaded attachment file ({len(res.content)} bytes, {content_type})")
                return (res.content, content_type)
            else:
                logger.warning(f"Failed to download attachment from {content_url}: HTTP {res.status_code}")
                return None
    except Exception as err:
        logger.warning(f"Error downloading attachment bytes: {err}")
        return None


def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch user profile details (displayName, mail, jobTitle, etc.) from Microsoft Graph."""
    token = get_access_token()
    if not token or not user_id:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/users/{user_id.strip()}"
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()
    except Exception as err:
        logger.debug(f"Could not fetch user profile for {user_id}: {err}")
    return None


async def async_get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
    """Async fetch user profile details from Microsoft Graph."""
    token = get_access_token()
    if not token or not user_id:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = f"{GRAPH_BASE_URL}/users/{user_id.strip()}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json()
    except Exception as err:
        logger.debug(f"Could not async fetch user profile for {user_id}: {err}")
    return None

