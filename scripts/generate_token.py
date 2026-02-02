#!/usr/bin/env python3
"""Generate Google OAuth token.json for the API proxy.

This script handles the OAuth flow to obtain credentials for accessing
the Gmail API. It opens a browser for authentication and saves the
resulting token to a file.

Usage:
    python scripts/generate_token.py --credentials credentials.json --output token.json

Requirements:
    pip install google-auth-oauthlib
"""

import argparse
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Error: google-auth-oauthlib is required.")
    print("Install it with: pip install google-auth-oauthlib")
    sys.exit(1)

# Gmail modify scope - allows reading emails and modifying labels
# Note: This scope also allows sending, which is why the proxy exists
# Calendar events scope - allows reading and writing calendar events
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]


def generate_token(credentials_file: Path, output_file: Path) -> None:
    """Run OAuth flow and save token to file.

    Args:
        credentials_file: Path to the OAuth client credentials JSON
        output_file: Path where the token should be saved
    """
    if not credentials_file.exists():
        print(f"Error: Credentials file not found: {credentials_file}")
        print()
        print("To get credentials:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create or select a project")
        print("3. Enable the Gmail API")
        print("4. Go to APIs & Services > Credentials")
        print("5. Create OAuth client ID (Desktop application)")
        print("6. Download the JSON file")
        sys.exit(1)

    print(f"Using credentials from: {credentials_file}")
    print(f"Token will be saved to: {output_file}")
    print()
    print("A browser window will open for Google authentication.")
    print("Sign in with the Google account you want to access.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)

    # Save the token
    with open(output_file, "w") as f:
        f.write(creds.to_json())

    # Set restrictive permissions
    output_file.chmod(0o600)

    print()
    print(f"Token saved to: {output_file}")
    print("Permissions set to 600 (owner read/write only)")
    print()
    print("You can now start the proxy with:")
    print(f"  api-proxy --token-file {output_file}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Google OAuth token for API proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --credentials credentials.json
  %(prog)s --credentials ~/Downloads/client_secret.json --output ~/.config/api-proxy/token.json

The credentials file is the OAuth client credentials downloaded from
Google Cloud Console. The output token file will be used by api-proxy
to authenticate with the Gmail API.
""",
    )

    parser.add_argument(
        "--credentials",
        "-c",
        type=Path,
        default=Path("credentials.json"),
        help="Path to OAuth client credentials JSON (default: credentials.json)",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("token.json"),
        help="Path for output token file (default: token.json)",
    )

    args = parser.parse_args()

    if args.output.exists():
        response = input(f"Token file {args.output} already exists. Overwrite? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 1

    try:
        generate_token(args.credentials, args.output)
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
