"""Subscription Management CLI.

Usage:
    python scripts/manage_subscription.py create
    python scripts/manage_subscription.py list
    python scripts/manage_subscription.py renew <subscription_id>
    python scripts/manage_subscription.py delete <subscription_id>

Requires .env to be configured with Microsoft credentials and WEBHOOK_PUBLIC_URL.
"""
import sys
import json
import argparse
from pathlib import Path

# Add project root to sys.path so we can import from src
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config import config
from src.graph_client import (
    create_subscription,
    renew_subscription,
    list_subscriptions,
    delete_subscription,
)


def handle_create():
    base_url = config.webhook_public_url
    if not base_url:
        print("ERROR: WEBHOOK_PUBLIC_URL is not configured in .env", file=sys.stderr)
        print("This must be a publicly accessible HTTPS URL.", file=sys.stderr)
        print("Example: https://your-tunnel.example.com", file=sys.stderr)
        sys.exit(1)

    notification_url = f"{base_url.rstrip('/')}/webhooks/teams"

    if config.teams.chat_id:
        print("Creating Graph subscription for Group Chat...")
        print(f"  Chat ID:          {config.teams.chat_id}")
        print(f"  Notification URL: {notification_url}\n")

        subscription = create_subscription(
            chat_id=config.teams.chat_id,
            notification_url=notification_url,
            expiration_minutes=60,
        )
    else:
        print("Creating Graph subscription for Channel...")
        print(f"  Team ID:          {config.teams.team_id}")
        print(f"  Channel ID:       {config.teams.channel_id}")
        print(f"  Notification URL: {notification_url}\n")

        subscription = create_subscription(
            team_id=config.teams.team_id,
            channel_id=config.teams.channel_id,
            notification_url=notification_url,
            expiration_minutes=60,
        )

    print("✓ Subscription created successfully!\n")
    print(f"  Subscription ID:  {subscription.get('id')}")
    print(f"  Resource:         {subscription.get('resource')}")
    print(f"  Change Type:      {subscription.get('changeType')}")
    print(f"  Expires:          {subscription.get('expirationDateTime')}")
    print(f"  Client State:     {subscription.get('clientState')}\n")
    print("IMPORTANT: This subscription expires in ~60 minutes.")
    print(f"Run 'python scripts/manage_subscription.py renew {subscription.get('id')}' to extend it.")


def handle_list():
    print("Listing active subscriptions...\n")
    subs = list_subscriptions()

    if not subs:
        print("No active subscriptions found.")
        return

    for i, sub in enumerate(subs, 1):
        print(f"--- Subscription {i} ---")
        print(f"  ID:         {sub.get('id')}")
        print(f"  Resource:   {sub.get('resource')}")
        print(f"  Change:     {sub.get('changeType')}")
        print(f"  Expires:    {sub.get('expirationDateTime')}")
        print(f"  URL:        {sub.get('notificationUrl')}\n")


def handle_renew(subscription_id: str):
    if not subscription_id:
        print("ERROR: Subscription ID is required.", file=sys.stderr)
        print("Usage: python scripts/manage_subscription.py renew <subscription_id>", file=sys.stderr)
        sys.exit(1)

    print(f"Renewing subscription {subscription_id}...")
    renewed = renew_subscription(subscription_id, expiration_minutes=60)
    print("✓ Subscription renewed!")
    print(f"  New expiration: {renewed.get('expirationDateTime')}")


def handle_delete(subscription_id: str):
    if not subscription_id:
        print("ERROR: Subscription ID is required.", file=sys.stderr)
        print("Usage: python scripts/manage_subscription.py delete <subscription_id>", file=sys.stderr)
        sys.exit(1)

    print(f"Deleting subscription {subscription_id}...")
    delete_subscription(subscription_id)
    print("✓ Subscription deleted.")


def main():
    parser = argparse.ArgumentParser(
        description="Microsoft Graph Subscription Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions:
  create                Create a new subscription for the configured team/channel
  list                  List all active subscriptions
  renew <id>            Renew/extend an existing subscription
  delete <id>           Delete a subscription
        """,
    )
    parser.add_argument("action", choices=["create", "list", "renew", "delete"], help="Action to perform")
    parser.add_argument("id", nargs="?", default=None, help="Subscription ID (required for renew and delete)")

    args = parser.parse_args()

    try:
        if args.action == "create":
            handle_create()
        elif args.action == "list":
            handle_list()
        elif args.action == "renew":
            handle_renew(args.id)
        elif args.action == "delete":
            handle_delete(args.id)
    except Exception as err:
        print(f"\nERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
