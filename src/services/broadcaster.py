import asyncio
from typing import List, Set
from fastapi import WebSocket
from src.logger import logger

_active_connections: Set[WebSocket] = set()


async def connect(websocket: WebSocket):
    await websocket.accept()
    _active_connections.add(websocket)


def disconnect(websocket: WebSocket):
    _active_connections.discard(websocket)


async def broadcast_message(message_data: dict):
    """Broadcast newly received or updated message to all connected dashboards.

    The `message_data` dict should contain a `type` key indicating the event kind
    (e.g., 'NEW_MESSAGE', 'MESSAGE_UPDATED', 'subscription_renewed').
    This type is forwarded to the frontend as the top-level `type` field so the
    dashboard WebSocket handler can correctly differentiate event types.
    """
    if not _active_connections:
        return

    # Use the caller-specified type so the frontend can distinguish events
    event_type = message_data.get("type", "NEW_MESSAGE")
    payload = {**message_data, "type": event_type}

    dead_connections = set()
    for ws in _active_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            dead_connections.add(ws)

    for ws in dead_connections:
        disconnect(ws)


async def broadcast_status(status_data: dict):
    """Broadcast status updates (subscription renewed, stats, etc.)."""
    if not _active_connections:
        return

    dead_connections = set()
    for ws in _active_connections:
        try:
            await ws.send_json({"type": "STATUS_UPDATE", "data": status_data})
        except Exception:
            dead_connections.add(ws)

    for ws in dead_connections:
        disconnect(ws)
