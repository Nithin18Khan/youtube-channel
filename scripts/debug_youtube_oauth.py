#!/usr/bin/env python3
"""
Diagnose YouTube OAuth / dual-channel routing issues.

Run locally:
  python scripts/debug_youtube_oauth.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS = BASE_DIR / "credentials"
CONFIG = BASE_DIR / "config" / "youtube.json"

EXPECTED = {
    "english": "UCJnH0aiSQRq2hODcMUwDJOg",
    "malayalam": "UCSvL2qB1WVJZi_7iOW8M3MA",
}


def _section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def check_files() -> bool:
    _section("1. Local files")
    ok = True
    client = CREDENTIALS / "youtube_client.json"
    print(f"  OAuth client:     {'OK' if client.exists() else 'MISSING'}  {client}")
    for key, ch_id in EXPECTED.items():
        token = CREDENTIALS / f"token_{key}.json"
        if not token.exists():
            print(f"  token_{key}:      MISSING")
            ok = False
            continue
        data = json.loads(token.read_text(encoding="utf-8"))
        saved_id = data.get("channel_id", "(not saved)")
        saved_title = data.get("channel_title", "?")
        match = saved_id == ch_id
        tag = "OK" if match else "WRONG"
        print(f"  token_{key}:      [{tag}] {saved_title} ({saved_id})")
        print(f"                    expected {ch_id}")
        if not match:
            ok = False
    return ok


def check_live_tokens() -> None:
    _section("2. Live API check (what Google thinks each token can upload to)")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        print("  Install deps: pip install google-auth google-auth-oauthlib google-api-python-client")
        return

    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    for key in EXPECTED:
        token_path = CREDENTIALS / f"token_{key}.json"
        if not token_path.exists():
            print(f"  {key.upper()}: no token file — skip")
            continue
        data = json.loads(token_path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(data, scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        yt = build("youtube", "v3", credentials=creds)
        resp = yt.channels().list(part="snippet", mine=True, maxResults=50).execute()
        items = resp.get("items", [])
        print(f"  {key.upper()} token sees {len(items)} channel(s):")
        for item in items:
            print(f"    - {item['snippet']['title']} ({item['id']})")
        if not items:
            print("    (none — token may be revoked; re-run OAuth)")


def print_manual_checks() -> None:
    _section("3. Manual checks (do these in Chrome)")
    print("""
  A) Same Google account owns BOTH channels?
     -> https://www.youtube.com/account
     -> Section: "Your channels"
     -> You should see BOTH:
          YouTube Channel English
          YouTube Channel Malayalam
     If English is missing here, OAuth cannot pick it with this Gmail.

  B) Are you Owner (not only Manager) on English?
     -> https://studio.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg
     -> Settings (gear) -> Permissions
     -> Your role must be "Owner"
     Managers cannot create upload tokens for that channel.

  C) Remove old app access BEFORE each OAuth retry
     -> https://myaccount.google.com/permissions
     -> Tab: "Access to Any account access"
     -> Remove "YouTube Upload" (your screenshot shows it exists)
     -> Also check tab: "Sign in with Google" for the same app

  D) During OAuth — the critical screen is AFTER Google "Allow"
     Flow:
       1. Google sign-in
       2. "way finder / YouTube Upload wants access" -> Allow
       3. *** YouTube "Select a channel" ***  <- pick ENGLISH here
       4. Browser redirects to localhost -> success
     Studio URL being English does NOT set step 3.
     If you skip step 3, Google defaults to Malayalam.

  E) Run OAuth in YOUR terminal (not Cursor agent chat)
     -> Cursor menu: Terminal -> New Terminal
     -> cd to project folder
     -> python scripts/setup_youtube_oauth.py --channel english --no-incognito --interactive
     -> Press ENTER when on English Studio, then complete browser flow
""")


def print_fix_steps() -> None:
    _section("4. Fix order")
    print("""
  1. Remove "YouTube Upload" from Linked apps (both tabs)
  2. Open English Studio — confirm URL has UCJnH0aiSQRq2hODcMUwDJOg
  3. Run English OAuth in your own terminal (interactive)
  4. On YouTube channel picker: click ENGLISH (not Malayalam)
  5. Verify:
       python scripts/verify_youtube_channels.py
  6. Sync GitHub secrets:
       powershell -File scripts/sync_github_secrets.ps1
  7. Re-upload Day 1 English:
       python upload_day.py --day 1 --lang en --force
""")


def main() -> None:
    print("YouTube OAuth debugger")
    files_ok = check_files()
    check_live_tokens()
    print_manual_checks()
    print_fix_steps()
    if not files_ok:
        sys.exit(1)
    print("\nLocal token files look correct. Run verify_youtube_channels.py to double-check.")


if __name__ == "__main__":
    main()
