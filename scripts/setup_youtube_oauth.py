#!/usr/bin/env python3
"""
One-time OAuth setup for YouTube upload (run locally in browser).

Run TWICE with same Google account — pick English channel first, Malayalam second:

  python scripts/setup_youtube_oauth.py --channel english
  python scripts/setup_youtube_oauth.py --channel malayalam

Add printed refresh tokens to GitHub Secrets:
  YOUTUBE_REFRESH_TOKEN_ENGLISH
  YOUTUBE_REFRESH_TOKEN_MALAYALAM
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = BASE_DIR / "credentials"
CLIENT_SECRET = CREDENTIALS_DIR / "youtube_client.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--channel",
        required=True,
        choices=("english", "malayalam"),
        help="Which channel to authorize (run once per channel)",
    )
    args = parser.parse_args()

    if not CLIENT_SECRET.exists():
        print(f"Missing {CLIENT_SECRET}")
        print("Download OAuth client JSON from Google Cloud Console → APIs → Credentials")
        print("Save as credentials/youtube_client.json")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    token_path = CREDENTIALS_DIR / f"token_{args.channel}.json"

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    print(f"\nBrowser opening — sign in and select your {args.channel.upper()} YouTube channel.\n")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    token_path.write_text(creds.to_json(), encoding="utf-8")
    data = json.loads(creds.to_json())

    print(f"Saved token: {token_path}\n")
    print("=" * 60)
    print(f"GitHub Secret for {args.channel.upper()}:")
    print("=" * 60)
    print(data.get("refresh_token", ""))
    print("=" * 60)
    print("\nAlso add to GitHub Secrets (from youtube_client.json):")
    print("  YOUTUBE_CLIENT_ID")
    print("  YOUTUBE_CLIENT_SECRET")
    print("  GEMINI_API_KEY")


if __name__ == "__main__":
    main()
