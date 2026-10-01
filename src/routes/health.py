import time
from datetime import datetime, timezone
from fastapi import APIRouter, Query, HTTPException
from src.repositories.message_repository import get_all_messages
from src.logger import logger

router = APIRouter()
start_time = time.time()


@router.get("/health")
def get_health():
    """Returns server health status."""
    return {
        "status": "ok",
        "service": "teams-mvp",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime": round(time.time() - start_time, 2),
    }


@router.get("/api/messages")
@router.get("/messages")
def list_stored_messages(limit: int = Query(50, ge=1, le=200)):
    """Returns recently stored messages (for development/testing inspection)."""
    try:
        messages = get_all_messages(limit=limit)
        return {"count": len(messages), "messages": messages}
    except Exception as err:
        logger.error(f"Failed to retrieve stored messages: {err}")
        raise HTTPException(status_code=500, detail="Failed to retrieve messages")
