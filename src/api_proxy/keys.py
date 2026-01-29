"""API key management CLI tool."""

import argparse
import sys
from pathlib import Path

from api_proxy.auth import APIKeyManager


def cmd_create(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """Create a new API key."""
    try:
        key = manager.create_key(args.name)
        print(f"Created API key '{args.name}': {key}")
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_list(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """List all API keys."""
    keys = manager.list_keys()

    if not keys:
        print("No API keys found.")
        return 0

    # Print header
    print(f"{'NAME':<20} {'CREATED':<20} {'LAST USED':<20} {'ENABLED':<8}")
    print("-" * 70)

    for key_info in keys:
        name = key_info["name"][:20]
        created = key_info["created_at"][:19] if key_info["created_at"] else "unknown"
        last_used = key_info["last_used_at"][:19] if key_info["last_used_at"] else "never"
        enabled = "yes" if key_info["enabled"] else "no"
        print(f"{name:<20} {created:<20} {last_used:<20} {enabled:<8}")

    return 0


def cmd_disable(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """Disable an API key."""
    if manager.set_enabled(args.name, False):
        print(f"Disabled API key '{args.name}'")
        return 0
    else:
        print(f"Error: API key '{args.name}' not found", file=sys.stderr)
        return 1


def cmd_enable(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """Enable an API key."""
    if manager.set_enabled(args.name, True):
        print(f"Enabled API key '{args.name}'")
        return 0
    else:
        print(f"Error: API key '{args.name}' not found", file=sys.stderr)
        return 1


def cmd_revoke(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """Revoke (permanently delete) an API key."""
    if manager.revoke_key(args.name):
        print(f"Revoked API key '{args.name}'")
        return 0
    else:
        print(f"Error: API key '{args.name}' not found", file=sys.stderr)
        return 1


def cmd_show(manager: APIKeyManager, args: argparse.Namespace) -> int:
    """Show details for a specific API key."""
    result = manager.get_key_by_name(args.name)

    if result is None:
        print(f"Error: API key '{args.name}' not found", file=sys.stderr)
        return 1

    key, key_data = result
    print(f"Name:       {key_data.get('name', 'unknown')}")
    print(f"Key:        {'*' * 28}{key[-4:]}")  # Mask all but last 4 chars
    print(f"Created:    {key_data.get('created_at', 'unknown')}")
    print(f"Last Used:  {key_data.get('last_used_at') or 'never'}")
    print(f"Enabled:    {'yes' if key_data.get('enabled', True) else 'no'}")

    return 0


def main() -> int:
    """Main entry point for the API key management CLI."""
    parser = argparse.ArgumentParser(
        prog="api-proxy-keys",
        description="Manage API keys for the API proxy server",
    )
    parser.add_argument(
        "--api-keys-file",
        type=Path,
        default=Path("api_keys.json"),
        help="Path to the API keys file (default: api_keys.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # create command
    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--name", required=True, help="Name for the API key")

    # list command
    subparsers.add_parser("list", help="List all API keys")

    # disable command
    disable_parser = subparsers.add_parser("disable", help="Disable an API key")
    disable_parser.add_argument("--name", required=True, help="Name of the API key to disable")

    # enable command
    enable_parser = subparsers.add_parser("enable", help="Enable an API key")
    enable_parser.add_argument("--name", required=True, help="Name of the API key to enable")

    # revoke command
    revoke_parser = subparsers.add_parser("revoke", help="Permanently delete an API key")
    revoke_parser.add_argument("--name", required=True, help="Name of the API key to revoke")

    # show command
    show_parser = subparsers.add_parser("show", help="Show details for an API key")
    show_parser.add_argument("--name", required=True, help="Name of the API key to show")

    args = parser.parse_args()
    manager = APIKeyManager(args.api_keys_file)

    commands = {
        "create": cmd_create,
        "list": cmd_list,
        "disable": cmd_disable,
        "enable": cmd_enable,
        "revoke": cmd_revoke,
        "show": cmd_show,
    }

    return commands[args.command](manager, args)


if __name__ == "__main__":
    sys.exit(main())
