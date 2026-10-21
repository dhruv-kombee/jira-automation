"""Server entry point.

Starts the FastAPI server with Uvicorn for receiving Microsoft Graph
change notifications for Teams channel messages.
"""
import os
import uvicorn
from src.config import config
from src.app import app

if __name__ == "__main__":
    is_prod = os.getenv("RENDER") is not None or os.getenv("ENV", "development").lower() in ("production", "prod")
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=config.port,
        reload=not is_prod,
    )
