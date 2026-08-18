#!/usr/bin/env python3
"""Print which YouTube channel each OAuth token is authorized for."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

EXPECTED = {
    "english": ("UCJnH0aiSQRq2hODcMUwDJOg", "English channel"),
    "malayalam": ("UCSvL2qB1WVJZi_7iOW8M3MA", "Malayalam channel"),
}


def main() -> None:
    ok = True
    for key, (expected_id, label) in EXPECTED.items():
        token_path = BASE_DIR / "credentials" / f"token_{key}.json"
        if not token_path.exists():
            print(f"[MISSING] {key.upper()} token file not found")
            ok = False
            continue
        data = json.loads(token_path.read_text(encoding="utf-8"))
        ch_id = data.get("channel_id")
        ch_title = data.get("channel_title", "?")
        if not ch_id:
            print(f"[UNKNOWN] {key.upper()} — re-run OAuth to save channel_id:")
            print(f"          python scripts/setup_youtube_oauth.py --channel {key}")
            ok = False
            continue
        match = ch_id == expected_id
        status = "OK" if match else "WRONG"
        if not match:
            ok = False
        print(f"[{status}] {key.upper():9} -> {ch_title} ({ch_id})")
        print(f"         expected -> {label} ({expected_id})")
        print()
    if not ok:
        print("Fix English token (most common issue):")
        print("  python scripts/setup_youtube_oauth.py --channel english")
        print("  Pick ENGLISH channel in Google picker (not Malayalam)")
        print("  powershell -File scripts/sync_github_secrets.ps1")
        sys.exit(1)
    print("All tokens match correct channels.")


if __name__ == "__main__":
    main()
