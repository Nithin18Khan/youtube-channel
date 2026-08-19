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


def is_single_channel_mode() -> bool:
    return bool(load_config().get("upload_mode", {}).get("single_channel"))


def primary_channel_key() -> str:
    return load_config().get("upload_mode", {}).get("primary_channel", "malayalam")


def _load_client_oauth() -> tuple[str, str]:
    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    client_path = BASE_DIR / "credentials" / "youtube_client.json"
    for name in ("youtube_client_web.json", "youtube_client_v2.json", "youtube_client.json"):
        candidate = BASE_DIR / "credentials" / name
        if candidate.exists():
            client_path = candidate
            break
    if client_path.exists():
        with client_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        block = data.get("installed") or data.get("web") or data
        return block["client_id"], block["client_secret"]

    raise RuntimeError(
        "Missing YouTube OAuth client — set YOUTUBE_CLIENT_ID/SECRET or save credentials/youtube_client.json"
    )


def _token_path(channel_key: str) -> Path:
    return BASE_DIR / "credentials" / f"token_{channel_key}.json"


def _config_channel_meta(channel_key: str) -> tuple[str | None, str | None]:
    """Channel id/title from config — used when CI secrets have no saved metadata."""
    try:
        ch = load_config()["channels"][channel_key]
        return ch.get("id"), ch.get("title") or ch.get("name")
    except (KeyError, FileNotFoundError, json.JSONDecodeError):
        return None, None


def _load_token_meta(channel_key: str) -> tuple[str, str | None, str | None]:
    """Return refresh_token and optional channel_id/title saved at OAuth time."""
    env_map = {
        "english": ("YOUTUBE_REFRESH_TOKEN_ENGLISH", "YOUTUBE_REFRESH_TOKEN_EN"),
        "malayalam": ("YOUTUBE_REFRESH_TOKEN_MALAYALAM", "YOUTUBE_REFRESH_TOKEN_ML"),
    }
    for env_name in env_map.get(channel_key, ()):
        token = os.environ.get(env_name, "").strip()
        if token:
            id_env = os.environ.get(f"YOUTUBE_CHANNEL_ID_{channel_key.upper()}", "").strip()
            title_env = os.environ.get(f"YOUTUBE_CHANNEL_TITLE_{channel_key.upper()}", "").strip()
            cfg_id, cfg_title = _config_channel_meta(channel_key)
            return (
                token,
                id_env or cfg_id,
                title_env or cfg_title,
            )

    token_path = _token_path(channel_key)
    if token_path.exists():
        with token_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        token = data.get("refresh_token", "").strip()
        if token:
            return token, data.get("channel_id"), data.get("channel_title")

    raise RuntimeError(
        f"Missing refresh token for {channel_key} — run scripts/setup_youtube_oauth.py --channel {channel_key}"
    )


def _load_refresh_token(channel_key: str) -> str:
    if is_single_channel_mode():
        channel_key = primary_channel_key()
    token, _, _ = _load_token_meta(channel_key)
    return token


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


def get_authenticated_channel(youtube) -> dict[str, str]:
    """Return {id, title} for the channel tied to this OAuth token."""
    resp = (
        youtube.channels()
        .list(part="snippet", mine=True, maxResults=1)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("OAuth token has no YouTube channel — pick a channel during sign-in")
    ch = items[0]
    return {
        "id": ch["id"],
        "title": ch["snippet"]["title"],
    }


def verify_token_channel(youtube, channel_key: str) -> dict[str, str]:
    """Ensure OAuth token matches config channel ID (uploads always go to token channel)."""
    config = load_config()
    expected = config["channels"][channel_key]
    expected_id = expected["id"]
    _, saved_id, saved_title = _load_token_meta(channel_key)

    if saved_id:
        actual = {"id": saved_id, "title": saved_title or saved_id}
    else:
        try:
            actual = get_authenticated_channel(youtube)
        except Exception as exc:
            log.warning(
                "Channel API verify skipped for %s (%s); trusting config %s",
                channel_key,
                exc,
                expected_id,
            )
            actual = {"id": expected_id, "title": expected.get("title", channel_key)}

    if actual["id"] != expected_id:
        raise RuntimeError(
            f"Wrong channel for {channel_key}! "
            f"Token is authorized for '{actual['title']}' ({actual['id']}), "
            f"but config expects {expected_id}. "
            f"Re-run: python scripts/setup_youtube_oauth.py --channel {channel_key} "
            f"and select the correct channel in the browser."
        )
    log.info("Channel verified %s: %s (%s)", channel_key, actual["title"], actual["id"])
    return actual


def upload_thumbnail(youtube, video_id: str, thumb_path: Path) -> None:
    if not thumb_path.is_file():
        log.warning("No thumbnail JPG: %s", thumb_path)
        return
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=False)
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    log.info("Thumbnail uploaded: %s → %s", thumb_path.name, video_id)


def _thumbnail_jpg_path(day: int, lang: str) -> Path:
    suffix = "Malayalam" if lang == "ml" else "English"
    return OUTPUT_DIR / f"Day_{day}_{suffix}_thumbnail.jpg"


def upload_captions(youtube, video_id: str, srt_path: Path, language: str) -> None:
    if not srt_path.exists():
        log.warning("No subtitle file: %s", srt_path)
        return
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "videoId": video_id,
            "language": language,
            "name": f"{language} captions",
            "isDraft": False,
        }
    }
    media = MediaFileUpload(str(srt_path), mimetype="application/octet-stream", resumable=False)
    youtube.captions().insert(
        part="snippet",
        body=body,
        media_body=media,
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
    verify_key = primary_channel_key() if is_single_channel_mode() else channel_key
    verify_token_channel(youtube, verify_key)
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

    log.info(
        "Uploading Day %s %s → channel %s%s",
        day,
        lang,
        config["channels"][verify_key]["id"],
        " (single-channel mode)" if is_single_channel_mode() else "",
    )
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

    thumb_path = _thumbnail_jpg_path(day, lang)
    try:
        upload_thumbnail(youtube, video_id, thumb_path)
    except Exception as exc:
        log.warning("Thumbnail upload failed (video still live): %s", exc)

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


def upload_day_thumbnails_only(day: int) -> dict[str, str]:
    """Upload thumbnail JPGs to already-live videos for one day."""
    token = _load_refresh_token(primary_channel_key())
    _load_client_oauth()
    state = load_upload_state()
    day_uploads = state.get("uploads", {}).get(str(day), {})
    if not day_uploads:
        raise RuntimeError(f"Day {day} not in upload_state — upload videos first")

    results: dict[str, str] = {}
    youtube = _build_youtube_service(token)
    for lang in ("en", "ml"):
        video_id = day_uploads.get(lang, {}).get("video_id")
        if not video_id:
            raise RuntimeError(f"No video_id for Day {day} {lang}")
        thumb = _thumbnail_jpg_path(day, lang)
        upload_thumbnail(youtube, video_id, thumb)
        results[lang] = video_id
    return results


def upload_day_both_channels(
    day: int, privacy_status: str = "public", *, force: bool = False, langs: tuple[str, ...] | None = None
) -> dict[str, str]:
    """Upload English + Malayalam for one day (separate channels or single-channel mode)."""
    token = _load_refresh_token(primary_channel_key())
    _load_client_oauth()
    if is_single_channel_mode():
        log.info(
            "Single-channel mode: EN + ML → %s (%s)",
            primary_channel_key(),
            load_config()["channels"][primary_channel_key()]["id"],
        )

    results: dict[str, str] = {}
    pairs = (
        ("en", "english", OUTPUT_DIR / f"Day_{day}_English.mp4", token),
        ("ml", "malayalam", OUTPUT_DIR / f"Day_{day}_Malayalam.mp4", token),
    )
    for lang, channel_key, video_path, token in pairs:
        if langs and lang not in langs:
            continue
        if not force and is_uploaded(day, lang):
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
