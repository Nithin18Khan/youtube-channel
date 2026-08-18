#!/usr/bin/env python3
"""Upload latest completed episode to both YouTube channels."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _pipeline_state_path() -> Path:
    env = os.environ.get("PIPELINE_STATE_FILE")
    if env:
        return Path(env)
    data_state = BASE_DIR / "data" / "state.json"
    if data_state.exists():
        return data_state
    return BASE_DIR / "state.json"


def completed_day(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    path = _pipeline_state_path()
    if not path.exists():
        raise FileNotFoundError(f"No pipeline state: {path}")
    with path.open(encoding="utf-8") as fh:
        current = int(json.load(fh).get("current_day", 1))
    return max(current - 1, 1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Upload Day N to English + Malayalam YouTube channels")
    parser.add_argument("--day", type=int, help="Episode day (default: last completed)")
    parser.add_argument(
        "--privacy",
        default=os.environ.get("YOUTUBE_PRIVACY", "public"),
        choices=("public", "unlisted", "private"),
    )
    args = parser.parse_args()

    day = completed_day(args.day)
    en_mp4 = BASE_DIR / "output" / f"Day_{day}_English.mp4"
    ml_mp4 = BASE_DIR / "output" / f"Day_{day}_Malayalam.mp4"
    for path in (en_mp4, ml_mp4):
        if not path.exists():
            logging.error("Missing video: %s — run main.py first", path)
            sys.exit(1)

    import youtube_upload

    results = youtube_upload.upload_day_both_channels(day, privacy_status=args.privacy)
    logging.info("Upload complete Day %s:", day)
    for lang, vid in results.items():
        logging.info("  %s: https://youtu.be/%s", lang, vid)


if __name__ == "__main__":
    main()
