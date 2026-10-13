"""Single-Command Launcher for Jira Automation Hub (Headless Service).

Usage:
    py run.py

This script:
  1. Checks or starts the public ngrok webhook tunnel.
  2. Launches the FastAPI backend engine.
  3. Automatically creates/ensures the Microsoft Graph change notification subscription.
  4. Runs continuous auto-renewal and SLA monitoring loops in the background.
"""
import sys
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
from src.tunnel import ensure_tunnel


def main():
    port = config.port

    print("=" * 60)
    print("   [*] JIRA AUTOMATION HUB - TEAMS TO JIRA ENGINE")
    print("=" * 60)
    print(f"  * Mode:              Headless Automation Service")
    print(f"  * Local Port:        http://127.0.0.1:{port}")
    print(f"  * Teams Target:      Team To Jira Ticket Creation")
    print(f"  * Webhook Receiver:  /webhooks/teams")

    # Pre-check / initialize tunnel
    try:
        tunnel_url = ensure_tunnel(port=port)
        if tunnel_url:
            print(f"  * Public Tunnel:     {tunnel_url}")
        else:
            print("  * Public Tunnel:     [!] ngrok not running / no public URL")
    except Exception as e:
        print(f"  * Public Tunnel:     [!] Notice: {e}")

    print("  * Auto-Subscription: Enabled (starts on server ready)")
    print("=" * 60)

    # Launch server
    uvicorn.run(
        "src.app:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_dirs=["src"],
    )


if __name__ == "__main__":
    main()
