#!/usr/bin/env python3
"""
Import a refresh token from Google OAuth Playground into credentials/token_<channel>.json

Use when setup_youtube_oauth.py keeps defaulting to the wrong channel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

EXPECTED = {
    "english": ("UCJnH0aiSQRq2hODcMUwDJOg", "YouTube Channel English"),
    "malayalam": ("UCSvL2qB1WVJZi_7iOW8M3MA", "YouTube Channel Malayalam"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, choices=("english", "malayalam"))
    parser.add_argument("--refresh-token", required=True, help="Refresh token from OAuth Playground")
    parser.add_argument(
        "--client",
        type=Path,
        default=BASE_DIR / "credentials" / "youtube_client_v2.json",
        help="OAuth client JSON (Desktop or Web)",
    )
    args = parser.parse_args()

    client_path = args.client if args.client.is_absolute() else BASE_DIR / args.client
    if not client_path.exists():
        print(f"Missing client: {client_path}")
        sys.exit(1)

    with client_path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    block = raw.get("installed") or raw.get("web") or raw

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    creds = Credentials(
        token=None,
        refresh_token=args.refresh_token.strip(),
        token_uri=block["token_uri"],
        client_id=block["client_id"],
        client_secret=block["client_secret"],
        scopes=scopes,
    )
    creds.refresh(Request())

    yt = build("youtube", "v3", credentials=creds)
    resp = yt.channels().list(part="snippet", mine=True, maxResults=50).execute()
    items = resp.get("items", [])
    if not items:
        print("ERROR: Token works but no YouTube channel visible.")
        sys.exit(1)

    print("Channels visible to this refresh token:")
    for item in items:
        print(f"  - {item['snippet']['title']} ({item['id']})")

    ch = items[0]
    ch_id = ch["id"]
    ch_title = ch["snippet"]["title"]
    expected_id, expected_name = EXPECTED[args.channel]

    out = {
        "token": creds.token,
        "refresh_token": args.refresh_token.strip(),
        "token_uri": block["token_uri"],
        "client_id": block["client_id"],
        "client_secret": block["client_secret"],
        "scopes": scopes,
        "channel_id": ch_id,
        "channel_title": ch_title,
    }

    token_path = BASE_DIR / "credentials" / f"token_{args.channel}.json"
    if ch_id != expected_id:
        print(f"\nERROR: Token is for {ch_title} ({ch_id})")
        print(f"Expected: {expected_name} ({expected_id})")
        print("Re-run OAuth Playground and pick the correct channel on the brand-account screen.")
        sys.exit(1)

    token_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nOK — saved {token_path}")
    print(f"GitHub secret: YOUTUBE_REFRESH_TOKEN_{args.channel.upper()}")


if __name__ == "__main__":
    main()
