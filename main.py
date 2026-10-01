"""Server entry point.

Starts the FastAPI server with Uvicorn for receiving Microsoft Graph
change notifications for Teams channel messages.
"""
import uvicorn
from src.config import config
from src.app import app

if __name__ == "__main__":
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=config.port,
        reload=True,
    )
