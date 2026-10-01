"""Single-Command Launcher for Jira Automation Hub.

Usage:
    py run.py

This script:
  1. Checks or starts the public ngrok tunnel.
  2. Ensures Microsoft Graph change notification subscription is active.
  3. Launches the FastAPI server with live WebSockets.
  4. Automatically opens the real-time monitoring dashboard in your browser.
"""
import sys
import time
import webbrowser
import threading
import uvicorn
from pathlib import Path

# Safe UTF-8 output on Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config import config


def open_browser_delayed(url: str, delay: float = 1.5):
    """Wait for server startup then launch browser."""
    def _open():
        time.sleep(delay)
        print(f"\n[+] Opening dashboard in browser: {url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def main():
    port = config.port
    dashboard_url = f"http://localhost:{port}"

    print("=" * 60)
    print("   [*] JIRA AUTOMATION HUB - TEAMS TO JIRA LIVE ENGINE")
    print("=" * 60)
    print(f"  * Local Dashboard:   {dashboard_url}")
    print(f"  * Teams Target:      Team To Jira Ticket Creation")
    print(f"  * Real-time Stream:  WebSocket + Graph Webhook")
    print("=" * 60)

    # Schedule browser opening
    open_browser_delayed(dashboard_url, delay=1.8)

    # Launch server
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
