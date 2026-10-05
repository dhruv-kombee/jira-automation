import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from src.config import config
from src.graph_client import (
    create_subscription,
    renew_subscription,
    list_subscriptions,
    delete_subscription,
)
import httpx
from src.logger import logger
from src.tunnel import get_active_tunnel_url


def check_tunnel_reachable(url: Optional[str]) -> bool:
    """Verify that the webhook tunnel URL is alive and accessible."""
    if not url:
        return False
    clean_url = url.rstrip('/')
    headers = {
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "teams-automation-probe",
    }
    try:
        # Check /health endpoint with short timeout
        health_url = f"{clean_url}/health"
        res = httpx.get(health_url, headers=headers, timeout=3.5)
        if res.status_code == 200:
            return True
    except Exception:
        pass

    try:
        # Fallback check on root URL
        res = httpx.get(clean_url, headers=headers, timeout=3.5)
        return res.status_code < 500 and res.status_code != 404
    except Exception:
        return False


_auto_renew_task: Optional[asyncio.Task] = None
_latest_subscription: Optional[Dict[str, Any]] = None
_last_renewed_at: Optional[str] = None
_last_checked_at: Optional[str] = None

# Auto-renew triggers when remaining time drops below this threshold (30 minutes)
AUTO_RENEW_THRESHOLD_SECONDS = 1800  # 30m
AUTO_RENEW_CHECK_INTERVAL = 30       # 30s check frequency


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # Handles 2026-10-01T05:29:53.017349Z or 2026-10-01T05:29:53Z
        clean_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


def get_active_subscription_info() -> Dict[str, Any]:
    """Retrieve the current active subscription status from Microsoft Graph."""
    global _latest_subscription, _last_checked_at
    _last_checked_at = datetime.now(timezone.utc).isoformat()

    try:
        subs = list_subscriptions()
        target_chat = config.teams.chat_id
        target_channel = config.teams.channel_id

        active_sub = None
        for sub in subs:
            res = sub.get("resource", "")
            # Resilient check for target chat or channel ID anywhere in the resource path
            if target_chat and target_chat in res:
                active_sub = sub
                break
            elif target_channel and target_channel in res:
                active_sub = sub
                break
            elif not target_chat and not target_channel:
                active_sub = sub
                break

        if active_sub:
            _latest_subscription = active_sub
            exp_dt = parse_iso_datetime(active_sub.get("expirationDateTime"))
            now_utc = datetime.now(timezone.utc)
            remaining_seconds = max(0, int((exp_dt - now_utc).total_seconds())) if exp_dt else 0

            return {
                "active": True,
                "id": active_sub.get("id"),
                "resource": active_sub.get("resource"),
                "notificationUrl": active_sub.get("notificationUrl"),
                "expirationDateTime": active_sub.get("expirationDateTime"),
                "remainingSeconds": remaining_seconds,
                "formattedRemaining": f"{remaining_seconds // 60}m {remaining_seconds % 60}s",
                "autoRenewEnabled": True,
                "autoRenewThresholdSeconds": AUTO_RENEW_THRESHOLD_SECONDS,
                "autoRenewThresholdText": "30m",
                "lastRenewedAt": _last_renewed_at,
                "lastCheckedAt": _last_checked_at,
            }
    except Exception as err:
        logger.debug(f"Failed to fetch active subscription: {err}")

    return {
        "active": False,
        "id": None,
        "resource": None,
        "notificationUrl": None,
        "expirationDateTime": None,
        "remainingSeconds": 0,
        "formattedRemaining": "None",
        "autoRenewEnabled": True,
        "autoRenewThresholdSeconds": AUTO_RENEW_THRESHOLD_SECONDS,
        "autoRenewThresholdText": "30m",
        "lastRenewedAt": _last_renewed_at,
        "lastCheckedAt": _last_checked_at,
    }


def ensure_subscription_online(public_url: Optional[str] = None) -> Dict[str, Any]:
    """Check if an active subscription exists. If not, or if expiring within 30 min, renew or create."""
    global _last_renewed_at
    tunnel_url = public_url or get_active_tunnel_url() or config.webhook_public_url
    if not tunnel_url:
        return {"status": "error", "message": "No public tunnel URL available"}

    # Verify tunnel is reachable before contacting Microsoft Graph
    if not check_tunnel_reachable(tunnel_url):
        logger.warning(
            f"Public webhook tunnel ({tunnel_url}) is offline or unreachable. "
            f"Cannot register Microsoft Graph subscription until tunnel is active.",
            extra={"event": "TUNNEL_UNREACHABLE", "url": tunnel_url},
        )
        return {
            "status": "error",
            "message": f"Webhook tunnel is offline: {tunnel_url}. Please ensure ngrok is running on port {config.port} or update WEBHOOK_PUBLIC_URL in .env.",
        }

    notification_url = f"{tunnel_url.rstrip('/')}/webhooks/teams"
    status = get_active_subscription_info()

    if status.get("active"):
        current_sub_url = (status.get("notificationUrl") or "").rstrip('/')
        # Verify the subscription is pointing to the current active tunnel
        if current_sub_url != notification_url.rstrip('/'):
            logger.info(
                f"Subscription points to outdated tunnel ({current_sub_url}). Re-creating for {notification_url}...",
                extra={"event": "SUBSCRIPTION_URL_MISMATCH"},
            )
            try:
                delete_subscription(status["id"])
            except Exception as del_err:
                logger.warning(f"Could not delete outdated subscription: {del_err}")
            # Will proceed to create fresh below
        else:
            # If subscription is active with time remaining (>5 min), report active and let async loop renew
            if status.get("remainingSeconds", 0) > 300:
                return {"status": "active", "subscription": status}

            # If critically low (<= 5 min), attempt synchronous refresh
            try:
                renewed = renew_subscription(status["id"], expiration_minutes=58)
                _last_renewed_at = datetime.now(timezone.utc).isoformat()
                return {"status": "renewed", "subscription": renewed}
            except Exception as err:
                logger.error(f"Failed to renew active subscription: {err}")
            return {"status": "active", "subscription": status}

    # Create new subscription
    try:
        if config.teams.chat_id:
            sub = create_subscription(
                chat_id=config.teams.chat_id,
                notification_url=notification_url,
                expiration_minutes=58,
            )
        else:
            sub = create_subscription(
                team_id=config.teams.team_id,
                channel_id=config.teams.channel_id,
                notification_url=notification_url,
                expiration_minutes=58,
            )
        _last_renewed_at = datetime.now(timezone.utc).isoformat()
        return {"status": "created", "subscription": sub}
    except Exception as err:
        logger.error(f"Failed to auto-create subscription: {err}")
        return {"status": "error", "message": str(err)}


async def auto_renew_loop():
    """Background task that runs continuously to monitor, auto-renew, and sync messages."""
    global _last_renewed_at
    logger.info(
        f"Subscription auto-renewer started ({AUTO_RENEW_CHECK_INTERVAL}s check cycle, triggers at < {AUTO_RENEW_THRESHOLD_SECONDS // 60}m)",
        extra={"event": "AUTO_RENEW_START"},
    )

    # Initial quick sync of recent messages
    await asyncio.sleep(2)
    try:
        from src.services.message_service import sync_recent_messages
        await sync_recent_messages(top=10)
    except Exception as e:
        logger.debug(f"Initial sync warning: {e}")

    while True:
        try:
            status = get_active_subscription_info()

            if status.get("active"):
                remaining = status.get("remainingSeconds", 0)
                sub_id = status["id"]

                # If remaining time is under 30 minutes, trigger auto-renew
                if remaining <= AUTO_RENEW_THRESHOLD_SECONDS:
                    logger.info(
                        f"Auto-renew triggered for subscription {sub_id} ({remaining}s remaining)...",
                        extra={"event": "AUTO_RENEW_TRIGGER", "remainingSeconds": remaining},
                    )
                    try:
                        from src.graph_client import async_renew_subscription
                        renewed = await async_renew_subscription(sub_id, expiration_minutes=58)
                        _last_renewed_at = datetime.now(timezone.utc).isoformat()
                        logger.info(
                            f"Subscription {sub_id} successfully auto-renewed!",
                            extra={"event": "AUTO_RENEW_SUCCESS", "newExpiry": renewed.get("expirationDateTime")},
                        )

                        # Broadcast live renewal update to dashboard
                        try:
                            from src.services.broadcaster import broadcast_message
                            await broadcast_message({
                                "type": "subscription_renewed",
                                "subscriptionId": sub_id,
                                "expirationDateTime": renewed.get("expirationDateTime"),
                                "remainingSeconds": 58 * 60,
                                "formattedRemaining": "58m 00s",
                                "message": "Graph subscription automatically renewed (+58m)!",
                            })
                        except Exception:
                            pass

                    except Exception as renew_err:
                        logger.warning(
                            f"Auto-renew attempt failed: {renew_err}.",
                            extra={"event": "AUTO_RENEW_FAIL"},
                        )
                        # Only attempt recreate if remaining time is critically low (< 5 min)
                        if remaining <= 300:
                            logger.info("Subscription critically low (<5m), ensuring online fresh...")
                            ensure_subscription_online()
            else:
                # If no active subscription or expired, verify tunnel is live before attempting create
                tunnel_url = get_active_tunnel_url() or config.webhook_public_url
                if tunnel_url and check_tunnel_reachable(tunnel_url):
                    logger.warning(
                        "No active subscription detected by auto-renewer loop. Ensuring online...",
                        extra={"event": "AUTO_RECREATE_TRIGGER"},
                    )
                    ensure_subscription_online(public_url=tunnel_url)
                else:
                    logger.debug("Auto-renewer loop waiting: public webhook tunnel is offline.")


        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.warning(f"Error in auto-renew loop: {err}")

        # Sleep for next check cycle
        await asyncio.sleep(AUTO_RENEW_CHECK_INTERVAL)


def start_auto_renew():
    global _auto_renew_task
    if _auto_renew_task is None or _auto_renew_task.done():
        _auto_renew_task = asyncio.create_task(auto_renew_loop())


def stop_auto_renew():
    global _auto_renew_task
    if _auto_renew_task and not _auto_renew_task.done():
        _auto_renew_task.cancel()
        _auto_renew_task = None
