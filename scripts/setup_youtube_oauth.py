#!/usr/bin/env python3
"""
One-time OAuth setup for YouTube upload (run locally in browser).

Same Gmail can own BOTH channels — run TWICE, pick a different channel each time:

  python scripts/setup_youtube_oauth.py --channel english
  python scripts/setup_youtube_oauth.py --channel malayalam
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = BASE_DIR / "credentials"
CLIENT_SECRET = CREDENTIALS_DIR / "youtube_client.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

CHANNEL_GUIDE = {
    "english": {
        "id": "UCJnH0aiSQRq2hODcMUwDJOg",
        "name": "YouTube Channel English",
        "handle": "@YouTubeChannelEnglish-666",
        "studio": "https://studio.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg",
        "open_urls": [
            "https://studio.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg",
            "https://www.youtube.com/@YouTubeChannelEnglish-666",
            "https://www.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg",
        ],
    },
    "malayalam": {
        "id": "UCSvL2qB1WVJZi_7iOW8M3MA",
        "name": "YouTube Channel Malayalam",
        "handle": "Malayalam channel",
        "studio": "https://studio.youtube.com/channel/UCSvL2qB1WVJZi_7iOW8M3MA",
        "open_urls": [
            "https://studio.youtube.com/channel/UCSvL2qB1WVJZi_7iOW8M3MA",
            "https://www.youtube.com/channel/UCSvL2qB1WVJZi_7iOW8M3MA",
        ],
    },
}


def _print_same_gmail_instructions(channel_key: str) -> None:
    guide = CHANNEL_GUIDE[channel_key]
    other_key = "malayalam" if channel_key == "english" else "english"
    other = CHANNEL_GUIDE[other_key]
    target_label = "ENGLISH" if channel_key == "english" else "MALAYALAM"
    print("\n" + "=" * 60)
    print("SAME GMAIL, TWO CHANNELS — READ BEFORE BROWSER OPENS")
    print("=" * 60)
    print("One Gmail can own many YouTube channels. Each upload token")
    print("works for ONE channel only — whichever you pick now.\n")
    print(f"For THIS run, authorize: {guide['name']}")
    print(f"  Studio: {guide['studio']}\n")
    print("Steps:")
    print(f"  1. Open the Studio link above — confirm it is the {target_label} channel")
    print("  2. When OAuth browser opens, sign in with your SAME Gmail")
    print("  3. BEFORE OAuth: on youtube.com click profile -> Switch account")
    print(f"     -> select {guide['name']} (NOT {other['name']})")
    print("  4. When OAuth opens, sign in with the SAME Gmail")
    print("  5. CRITICAL — Google may show 'Choose a channel':")
    print(f"       CLICK -> {guide['name']} ({guide['handle']})")
    print(f"     Do NOT click -> {other['name']}")
    print("  6. If you only see Malayalam, click Cancel and switch channel first")
    print("=" * 60 + "\n")


class _ChromeIncognitoBrowser:
    def __init__(self, chrome_exe: Path) -> None:
        self.chrome_exe = chrome_exe

    def open(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
        import subprocess

        subprocess.Popen(
            [str(self.chrome_exe), "--incognito", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True


def _register_chrome_incognito(chrome_exe: Path) -> str:
    import webbrowser

    webbrowser.register(
        "chrome_incognito",
        None,
        _ChromeIncognitoBrowser(chrome_exe),
        preferred=True,
    )
    return "chrome_incognito"
def _open_oauth_in_chrome() -> tuple[str, Path | None]:
    """Register and return Chrome browser name for OAuth (Windows)."""
    import webbrowser

    chrome_paths = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    for chrome_exe in chrome_paths:
        if chrome_exe.is_file():
            webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(str(chrome_exe)))
            return "chrome", chrome_exe
    return "windows-default", None


def _open_urls_in_chrome(urls: list[str], chrome_exe: Path | None) -> None:
    import subprocess

    for url in urls:
        if chrome_exe and chrome_exe.is_file():
            subprocess.Popen(
                [str(chrome_exe), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            import webbrowser

            webbrowser.open(url)
        print(f"Opened in Chrome: {url}")
        time.sleep(0.8)


def _countdown_wait(seconds: int, channel_key: str) -> None:
    label = "ENGLISH" if channel_key == "english" else "MALAYALAM"
    print(f"\nSwitch to {label} channel in Chrome if needed (profile -> Switch account).")
    for remaining in range(seconds, 0, -1):
        print(f"  OAuth opens in {remaining}s...", end="\r", flush=True)
        time.sleep(1)
    print("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--channel",
        required=True,
        choices=("english", "malayalam"),
        help="Which channel to authorize (run once per channel)",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=15,
        help="Seconds to wait after opening channel in Chrome before OAuth (default 15)",
    )
    parser.add_argument(
        "--no-incognito",
        action="store_true",
        help="Use normal Chrome (not incognito) — switch to the right channel in that profile first",
    )
    parser.add_argument(
        "--client",
        type=Path,
        default=CLIENT_SECRET,
        help="OAuth client JSON (use a NEW Desktop client from Google Cloud if channel picker never appears)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Wait for you to press Enter before opening OAuth (after switching channel)",
    )
    args = parser.parse_args()

    client_path = args.client if args.client.is_absolute() else BASE_DIR / args.client
    if not client_path.exists():
        print(f"Missing OAuth client: {client_path}")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    token_path = CREDENTIALS_DIR / f"token_{args.channel}.json"

    _print_same_gmail_instructions(args.channel)
    guide = CHANNEL_GUIDE[args.channel]
    browser, chrome_exe = _open_oauth_in_chrome()
    target_label = "ENGLISH" if args.channel == "english" else "MALAYALAM"
    print(f"\nAuto-opening {target_label} channel in Chrome...\n")
    # Revoke old app link first — forces Google to show channel picker again
    revoke_url = "https://myaccount.google.com/permissions"
    print("IMPORTANT: In Chrome, remove access for 'YouTube Upload' / 'way finder' app")
    print(f"  -> {revoke_url}\n")
    _open_urls_in_chrome([revoke_url], chrome_exe)
    time.sleep(2)
    _open_urls_in_chrome(guide.get("open_urls", [guide["studio"]]), chrome_exe)
    if args.interactive:
        print(f"\n>>> Switch to {target_label} on youtube.com (profile -> Switch account -> {guide['name']})")
        print(f">>> Confirm Studio URL ends with {guide['id']}")
        input("\nPress ENTER when ready for Google sign-in... ")
    else:
        _countdown_wait(max(args.wait, 5), args.channel)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
    oauth_browser = browser
    if browser == "chrome" and chrome_exe and not args.no_incognito:
        oauth_browser = _register_chrome_incognito(chrome_exe)
        print(f"\nOpening Google sign-in in CHROME INCOGNITO — pick {target_label} channel!\n")
    elif browser == "chrome":
        print(f"\nOpening Google sign-in in CHROME — pick {target_label} channel if asked...\n")
        print(f"Make sure you already switched to {guide['name']} on youtube.com in this Chrome profile.\n")
    else:
        print("\nOpening Google sign-in in default browser...\n")
    print("=" * 60)
    print("AFTER you click Allow / Continue on Google:")
    print("  YouTube opens a SECOND screen — 'Select a channel'")
    print(f"  You MUST click: {guide['name']}")
    other_key = "malayalam" if args.channel == "english" else "english"
    print(f"  Do NOT click: {CHANNEL_GUIDE[other_key]['name']}")
    print("  Do NOT close the browser until that screen appears!")
    print("=" * 60 + "\n")
    oauth_prompt = "select_account consent" if args.channel == "english" else "consent"
    creds = flow.run_local_server(
        port=0,
        prompt=oauth_prompt,
        access_type="offline",
        open_browser=True,
        browser=oauth_browser if browser != "windows-default" else None,
    )

    yt = build("youtube", "v3", credentials=creds)
    all_ch = yt.channels().list(part="snippet", mine=True, maxResults=50).execute()
    print("Channels visible to this token:")
    for item in all_ch.get("items", []):
        print(f"  - {item['snippet']['title']} ({item['id']})")
    ch_resp = all_ch
    ch = ch_resp["items"][0]
    ch_id = ch["id"]
    ch_title = ch["snippet"]["title"]
    expected = CHANNEL_GUIDE[args.channel]["id"]

    data = json.loads(creds.to_json())
    data["channel_id"] = ch_id
    data["channel_title"] = ch_title
    token_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(f"Authorized channel: {ch_title} ({ch_id})")
    if ch_id != expected:
        print("\n*** ERROR: Wrong channel selected! Token NOT saved. ***")
        print(f"Expected: {CHANNEL_GUIDE[args.channel]['name']} ({expected})")
        print(f"You picked: {ch_title} ({ch_id})")
        print("\nWhy this happens:")
        print("  Google skipped the 'Select a channel' screen and used your default channel.")
        print("\nFix (try in order):")
        print("  1. English Studio -> Settings -> Permissions -> you must be OWNER (not Manager)")
        print("  2. Remove 'YouTube Upload' from https://myaccount.google.com/permissions")
        print("  3. Google Cloud -> Credentials -> create NEW 'Desktop app' OAuth client")
        print("     Download JSON -> credentials/youtube_client_v2.json")
        print("     Run: python scripts/setup_youtube_oauth.py --channel english")
        print("            --client credentials/youtube_client_v2.json --interactive")
        print("  4. On fresh client, Google should show channel picker AFTER Continue")
        token_path.unlink(missing_ok=True)
        sys.exit(1)

    print(f"Channel match OK for {args.channel}.\n")
    print(f"Saved token: {token_path}\n")
    print("=" * 60)
    print(f"GitHub Secret: YOUTUBE_REFRESH_TOKEN_{args.channel.upper()}")
    print("=" * 60)
    print(data.get("refresh_token", ""))
    print("=" * 60)


if __name__ == "__main__":
    main()
