#!/usr/bin/env python3
"""YouTube upload helpers — dual-channel, CTR-optimized metadata."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("youtube-upload")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "config" / "youtube.json"
UPLOAD_STATE_PATH = Path(
    os.environ.get("UPLOAD_STATE_FILE", str(BASE_DIR / "data" / "upload_state.json"))
)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"

EN_TAGS = [
    "God of War",
    "Kratos",
    "cinematic gaming",
    "gaming story",
    "epic trailer",
    "Ghost of Sparta",
    "mythology",
    "action cinematic",
    "gaming shorts",
    "God of War Ragnarok style",
]

ML_TAGS = [
    "God of War Malayalam",
    "Kratos Malayalam",
    "gaming Malayalam",
    "cinematic Malayalam",
    "Malayalam gaming story",
    "ഗെയിമിംഗ്",
    "ക്രാറ്റോസ്",
    "സിനിമാറ്റിക്",
    "Malayalam shorts",
    "epic Malayalam",
]


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_client_oauth() -> tuple[str, str]:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    client_path = BASE_DIR / "credentials" / "youtube_client.json"
    if client_path.exists():
        with client_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        block = data.get("installed") or data.get("web") or data
        return block["client_id"], block["client_secret"]

    raise RuntimeError(
        "Missing YouTube OAuth client — set YOUTUBE_CLIENT_ID/SECRET or save credentials/youtube_client.json"
    )


def _load_refresh_token(channel_key: str) -> str:
    env_map = {
        "english": ("YOUTUBE_REFRESH_TOKEN_ENGLISH", "YOUTUBE_REFRESH_TOKEN_EN"),
        "malayalam": ("YOUTUBE_REFRESH_TOKEN_MALAYALAM", "YOUTUBE_REFRESH_TOKEN_ML"),
    }
    for env_name in env_map.get(channel_key, ()):
        token = os.environ.get(env_name, "").strip()
        if token:
            return token

    token_path = BASE_DIR / "credentials" / f"token_{channel_key}.json"
    if token_path.exists():
        with token_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        token = data.get("refresh_token", "").strip()
        if token:
            return token

    raise RuntimeError(
        f"Missing refresh token for {channel_key} — run scripts/setup_youtube_oauth.py --channel {channel_key}"
    )


def load_upload_state() -> dict[str, Any]:
    if UPLOAD_STATE_PATH.exists():
        with UPLOAD_STATE_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {"uploads": {}}


def save_upload_state(state: dict[str, Any]) -> None:
    UPLOAD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with UPLOAD_STATE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def parse_thumbnail_txt(path: Path) -> dict[str, str]:
    """Parse Day_N_*_thumbnail.txt into title fields."""
    data = {
        "primary": "",
        "secondary": "",
        "episode_title": "",
        "upload_title": "",
        "alt_hook": "",
    }
    if not path.exists():
        return data
    text = path.read_text(encoding="utf-8")
    patterns = {
        "primary": r"PRIMARY TEXT[^\n]*:\n(.+)",
        "secondary": r"SECONDARY TEXT[^\n]*:\n(.+)",
        "episode_title": r"EPISODE TITLE[^\n]*:\n(.+)",
        "upload_title": r"SUGGESTED YOUTUBE UPLOAD TITLE:\n(.+)",
        "alt_hook": r"ALT HOOK[^\n]*:\n(.+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            data[key] = match.group(1).strip()
    return data


def _episode_meta(day: int, lang: str) -> dict[str, Any]:
    import main

    episode = main.get_episode(day)
    is_ml = lang == "ml"
    return {
        "title": episode.get("title_ml" if is_ml else "title", f"Day {day}"),
        "hook": episode.get("hook_ml" if is_ml else "hook_en", ""),
    }


def build_title(day: int, lang: str) -> str:
    thumb_path = OUTPUT_DIR / f"Day_{day}_{'Malayalam' if lang == 'ml' else 'English'}_thumbnail.txt"
    parsed = parse_thumbnail_txt(thumb_path)
    title = parsed.get("upload_title") or parsed.get("primary") or ""
    if not title:
        meta = _episode_meta(day, lang)
        hook = meta["hook"]
        ep_title = meta["title"]
        title = f"{hook} | {ep_title}" if hook and hook.upper() != ep_title.upper() else ep_title
    # YouTube title max 100 chars
    prefix = f"Day {day} — "
    full = title if title.lower().startswith(f"day {day}") else f"{prefix}{title}"
    return full[:100]


def build_description(day: int, lang: str) -> str:
    meta = _episode_meta(day, lang)
    thumb_path = OUTPUT_DIR / f"Day_{day}_{'Malayalam' if lang == 'ml' else 'English'}_thumbnail.txt"
    parsed = parse_thumbnail_txt(thumb_path)
    hook = parsed.get("primary") or meta["hook"] or meta["title"]

    if lang == "ml":
        return f"""🎮 Ghost of Sparta — ദിവസം {day} | {meta['title']}

{hook}

▬ ഈ സീരീസ് ▬
30 ദിവസം — ഗ്രീക്ക് മുതൽ നോഴ്സ് വരെ സിനിമാറ്റിക് ഗെയിമിംഗ് കഥ.

▬ ഇന്ന് ▬
Episode {day} of 30 — daily upload 6:30 PM IST

▬ Subscribe ▬
പുതിയ എപ്പിസോഡ് প্রതിദിനം — Subscribe + Bell 🔔

#GodOfWar #Kratos #MalayalamGaming #GamingMalayalam #Cinematic #Epic #Shorts #ക്രാറ്റോസ് #ഗെയിമിംഗ്

---
Fan-made cinematic story. Not affiliated with Sony / Santa Monica Studio.
"""

    return f"""🎮 Ghost of Sparta — Day {day} | {meta['title']}

{hook}

▬ About this series ▬
30-day cinematic gaming saga — Greek vengeance to the Norse realm. New episode every day.

▬ Today ▬
Episode {day} of 30 — daily upload 6:30 PM IST (peak watch time)

▬ Subscribe for daily episodes ▬
🔔 Turn on notifications — never miss the next chapter.

#GodOfWar #Kratos #Gaming #Cinematic #Epic #GhostOfSparta #GamingStory #Shorts #GameTrailer

▬ Tags ▬
god of war, kratos cinematic, gaming story, epic gaming, mythology, norse, greek

---
Fan-made cinematic story. Not affiliated with Sony / Santa Monica Studio.
"""


def build_tags(lang: str) -> list[str]:
    base = ML_TAGS if lang == "ml" else EN_TAGS
    return base[:15]


def _youtube_credentials(refresh_token: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    client_id, client_secret = _load_client_oauth()
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _build_youtube_service(refresh_token: str):
    from googleapiclient.discovery import build

    creds = _youtube_credentials(refresh_token)
    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=creds)


def upload_captions(youtube, video_id: str, srt_path: Path, language: str) -> None:
    if not srt_path.exists():
        log.warning("No subtitle file: %s", srt_path)
        return
    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": f"{language} captions",
            "isDraft": False,
        }
    }
    with srt_path.open("rb") as fh:
        youtube.captions().insert(
            part="snippet",
            body=body,
            media_body=fh,
        ).execute()
    log.info("Captions uploaded (%s): %s", language, srt_path.name)


def upload_video(
    *,
    video_path: Path,
    day: int,
    lang: str,
    channel_key: str,
    refresh_token: str,
    privacy_status: str = "public",
) -> str:
    """Upload one video; returns YouTube video ID."""
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    config = load_config()
    channel = config["channels"][channel_key]
    upload_cfg = config["upload"]

    youtube = _build_youtube_service(refresh_token)
    title = build_title(day, lang)
    description = build_description(day, lang)
    tags = build_tags(lang)

    body: dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": upload_cfg.get("category_id", "20"),
            "defaultLanguage": channel.get("language", lang),
            "defaultAudioLanguage": channel.get("default_audio_language", lang),
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": upload_cfg.get("made_for_kids", False),
            "embeddable": True,
            "license": "youtube",
        },
    }

    log.info("Uploading Day %s %s → channel %s", day, lang, channel["id"])
    log.info("Title: %s", title)

    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(video_path), chunksize=1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            log.info("Upload progress: %s%%", pct)

    video_id = response["id"]
    log.info("Uploaded video ID: %s", video_id)

    suffix = channel.get("subtitle_suffix", f"{lang}.srt")
    srt_path = video_path.with_suffix(f".{suffix}")
    if not srt_path.exists():
        srt_path = OUTPUT_DIR / f"Day_{day}_{channel['video_suffix']}.{suffix}"
    try:
        upload_captions(youtube, video_id, srt_path, lang if lang != "ml" else "ml")
    except Exception as exc:
        log.warning("Caption upload failed (video still live): %s", exc)

    return video_id


def record_upload(day: int, lang: str, video_id: str) -> None:
    state = load_upload_state()
    uploads = state.setdefault("uploads", {})
    day_key = str(day)
    uploads.setdefault(day_key, {})
    uploads[day_key][lang] = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    save_upload_state(state)


def is_uploaded(day: int, lang: str) -> bool:
    state = load_upload_state()
    return lang in state.get("uploads", {}).get(str(day), {})


def upload_day_both_channels(day: int, privacy_status: str = "public") -> dict[str, str]:
    """Upload English + Malayalam for one day to separate channels."""
    en_token = _load_refresh_token("english")
    ml_token = _load_refresh_token("malayalam")
    _load_client_oauth()

    results: dict[str, str] = {}
    pairs = (
        ("en", "english", OUTPUT_DIR / f"Day_{day}_English.mp4", en_token),
        ("ml", "malayalam", OUTPUT_DIR / f"Day_{day}_Malayalam.mp4", ml_token),
    )
    for lang, channel_key, video_path, token in pairs:
        if is_uploaded(day, lang):
            vid = load_upload_state()["uploads"][str(day)][lang]["video_id"]
            log.info("Day %s %s already uploaded (%s), skipping", day, lang, vid)
            results[lang] = vid
            continue
        video_id = upload_video(
            video_path=video_path,
            day=day,
            lang=lang,
            channel_key=channel_key,
            refresh_token=token,
            privacy_status=privacy_status,
        )
        record_upload(day, lang, video_id)
        results[lang] = video_id
    return results
