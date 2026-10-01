"""CLI Utility to test Atlassian Jira Cloud connection and ticket creation.

Usage:
  py scripts/test_jira.py
  py scripts/test_jira.py --create
"""
import asyncio
import sys
import argparse
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config
from src.services.jira_service import test_jira_connection, create_jira_issue


async def main():
    parser = argparse.ArgumentParser(description="Test Jira Cloud integration")
    parser.add_argument("--create", action="store_true", help="Create a test issue in Jira if connection succeeds")
    args = parser.parse_args()

    print("=" * 60)
    print(" Jira Cloud Integration Diagnostic & Test Tool")
    print("=" * 60)
    print(f"Jira Base URL   : {config.jira.base_url or '(Not set)'}")
    print(f"Jira User Email : {config.jira.email or '(Not set)'}")
    print(f"Jira API Token  : {'*' * 8 if config.jira.api_token else '(Not set)'}")
    print(f"Jira Project Key: {config.jira.project_key or '(Not set)'}")
    print("-" * 60)

    if not config.jira.is_configured:
        print("[!] Jira configuration is incomplete in your .env file.")
        print("    Please set:")
        print("      JIRA_BASE_URL=https://your-domain.atlassian.net")
        print("      JIRA_EMAIL=your-email@example.com")
        print("      JIRA_API_TOKEN=your-atlassian-api-token")
        print("      JIRA_PROJECT_KEY=YOUR_PROJECT_KEY")
        print("=" * 60)
        sys.exit(1)

    print("\n[+] Testing connection to Jira Cloud...")
    res = await test_jira_connection()

    if not res.get("connected"):
        print(f"\n[-] CONNECTION FAILED: {res.get('error')}")
        sys.exit(1)

    user = res.get("user", {})
    project = res.get("project", {})
    print(f"[OK] Connected successfully as: {user.get('name')} ({user.get('email')})")
    print(f"[OK] Project Verified: {project.get('name')} [{project.get('key')}]")

    issue_types = project.get("issue_types", [])
    if issue_types:
        print(f"[OK] Available Issue Types: {', '.join(issue_types)}")

    if args.create:
        print("\n[+] Creating a test issue in Jira...")
        ticket_res = await create_jira_issue(
            summary="[Test] Microsoft Teams Automation Integration Test",
            description="### Verification Issue\nThis test ticket was created automatically to verify that the Teams-to-Jira automation engine is working properly.",
            issue_type=config.jira.default_issue_type,
            priority="Medium",
            labels=["teams-automation", "integration-test"],
        )
        if ticket_res.get("success"):
            print(f"\n[SUCCESS] Created Jira Issue: {ticket_res.get('key')}")
            print(f"          URL: {ticket_res.get('url')}")
        else:
            print(f"\n[-] Failed to create ticket: {ticket_res.get('error')}")
            sys.exit(1)
    else:
        print("\n[i] Connection is verified! To create a test ticket, run:")
        print("    py scripts/test_jira.py --create")

    print("=" * 60)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    asyncio.run(main())
