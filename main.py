#!/usr/bin/env python3
"""
30-Day Automated Video Generation System
Ghost of Sparta: Greek Vengeance to the Norse Realm

INSTALLATION
------------
    pip install edge-tts moviepy requests pillow

OPTIONAL (ffmpeg must be on PATH for MoviePy):
    Windows: winget install ffmpeg
    macOS:   brew install ffmpeg
    Linux:   sudo apt install ffmpeg

USAGE
-----
    python main.py

Each run processes ONE episode (tracked in state.json), then increments the day.
Outputs: output/Day_{N}_English.mp4 and output/Day_{N}_Malayalam.mp4
Burned-in subtitles match each video language; sidecar .en.srt / .ml.srt files are also exported.
Thumbnail copy: output/Day_{N}_English_thumbnail.txt and output/Day_{N}_Malayalam_thumbnail.txt

CINEMATIC SCRIPTS (recommended — 10–12 scenes, ~2 min):
    scripts/day_01_script.json ... scripts/day_30_script.json
    Scene array is sorted by id. Scenes auto-split into sub-shots when narration
    exceeds 4s or complex_action is set. Per-scene BGM cues: atmospheric, combat,
    epic_hook. Camera motion: zoom_in, zoom_out, pan_left, pan_right, whip_pan,
    tracking_shot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import numpy as np
import requests

try:
    import edge_tts
except ImportError:
    print("Missing dependency: pip install edge-tts")
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Missing dependency: pip install pillow")
    sys.exit(1)

# MoviePy 1.x / 2.x compatibility
try:
    from moviepy.editor import (
        AudioFileClip,
        CompositeAudioClip,
        VideoClip,
        concatenate_audioclips,
        concatenate_videoclips,
    )
except ImportError:
    from moviepy import (  # type: ignore
        AudioFileClip,
        CompositeAudioClip,
        VideoClip,
        concatenate_audioclips,
        concatenate_videoclips,
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
OUTPUT_DIR = BASE_DIR / "output"
STAGING_DIR = OUTPUT_DIR / ".staging"
TEMP_DIR = BASE_DIR / "temp"
TEMP_IMAGES_DIR = TEMP_DIR / "images"
TEMP_AUDIO_DIR = TEMP_DIR / "audio"
MOVIEPY_TEMP_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "ghost_video_mpy"
SCRIPTS_DIR = BASE_DIR / "scripts"
ASSETS_DIR = BASE_DIR / "assets"
BGM_DIR = ASSETS_DIR / "bgm"
BGM_PATH = ASSETS_DIR / "epic_bgm.mp3"  # legacy fallback
BGM_DOWNLOAD_URLS = [
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Clash%20Defiant.mp3",
    "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Volatile%20Reaction.mp3",
]
BGM_CUE_CONFIG: dict[str, dict[str, Any]] = {
    "atmospheric": {
        "file": "atmospheric.mp3",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/District%20Four.mp3",
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Drone%20in%20D.mp3",
        ],
        "volume": 0.17,
        "beat_offset": 0.0,
        "restart_per_shot": False,
        "crescendo": False,
    },
    "combat": {
        "file": "combat.mp3",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Clash%20Defiant.mp3",
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Break%20You%20In.mp3",
        ],
        "volume": 0.24,
        "beat_offset": 1.15,
        "restart_per_shot": True,
        "crescendo": False,
    },
    "epic_hook": {
        "file": "epic_hook.mp3",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Volatile%20Reaction.mp3",
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Hero%20Down.mp3",
        ],
        "volume": 0.25,
        "beat_offset": 0.0,
        "restart_per_shot": False,
        "crescendo": True,
    },
    "default": {
        "file": "default.mp3",
        "urls": BGM_DOWNLOAD_URLS,
        "volume": 0.20,
        "beat_offset": 0.0,
        "restart_per_shot": False,
        "crescendo": False,
    },
}
BGM_CUE_BY_SCENE_TYPE = {
    "hook": "epic_hook",
    "action": "combat",
    "establishing": "atmospheric",
}

VOICE_EN = "en-US-ChristopherNeural"
VOICE_ML = "ml-IN-SobhanaNeural"
VOICE_EN_RATE = "+2%"
VOICE_EN_PITCH = "+0Hz"
VOICE_ML_RATE = "+4%"
VOICE_ML_PITCH = "+2Hz"

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 24
SCENE_COUNT_MIN = 10
SCENE_COUNT_MAX = 12
IMAGE_DOWNLOAD_RETRIES = 3
IMAGE_DOWNLOAD_TIMEOUT = 120
REQUEST_DELAY_SEC = 1.5
KEN_BURNS_ZOOM_END = 1.22
LETTERBOX_BAR_HEIGHT = 120
TARGET_DURATION_SEC = 120
DEFAULT_BGM_VOLUME = 0.20
BGM_VOLUME_MIN = 0.15
BGM_VOLUME_MAX = 0.25
SUB_SHOT_THRESHOLD_SEC = 3.5
SUB_SHOT_MAX = 4
SUB_SHOT_MIN = 2
WHIP_PAN_ZOOM = 1.26
TRACKING_ZOOM = 1.10
SUBTITLE_FONT_SIZE_EN = 40
SUBTITLE_FONT_SIZE_ML = 44
SUBTITLE_MAX_LINES = 3
SUBTITLE_BOTTOM_PADDING = 36

# Legacy flat-script episodes still use a fixed image count.
IMAGE_COUNT = 10

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt/{prompt}?width={w}&height={h}&nologo=true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ghost-of-sparta")

# ---------------------------------------------------------------------------
# 30-Day Story Arc (embedded below)
# ---------------------------------------------------------------------------

EPISODE_DATA: list[dict[str, Any]] = [{'day': 1, 'title': 'The Ghost of Sparta Awakens', 'script_en': 'They call him the Ghost of Sparta. Kratos walks ruined cliffs above the Aegean while memory hunts him like wolves. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'അവര്\u200d അയാളെ സ്പാർട്ടയുടെ പ്രേതം എന്ന് വിളിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, moonlit Spartan warrior on Aegean cliffs, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares offering chains in battlefield smoke, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan family memory in burning village, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, white ash falling over ancient temple, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior eyes reflecting blood moon, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, breaking divine chains close-up, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost silhouette over burning Greece map, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Blades of Chaos igniting on forearms, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Mount Olympus foreshadow in storm clouds, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical poster lone warrior horizon, hyper-detailed dramatic lighting 8k']}, {'day': 2, 'title': 'Chains of the War God', 'script_en': 'Before dawn, Kratos marches to a shrine where Ares demands blood and obedience carved into bone. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'പ്രഭാതത്തിന് മുമ്പ് ഏറസ് രക്തവും കീഴ്പ്പാടും ആവശ്യപ്പെടുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, hidden war shrine bronze braziers, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares god of war towering in fire, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Blades of Chaos first binding scene, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan army marching through misty pines, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior kneeling invisible god whispering, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, white ash dream over Sparta, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, blood on chained wrists macro, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Delphi path ravens and broken statues, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, river reflection split warrior ghost, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, counting chain links close-up, hyper-detailed dramatic lighting 8k']}, {'day': 3, 'title': 'Oracle Temple Trickery', 'script_en': 'At Delphi, Ares orders Kratos to silence the Oracle before she names the betrayal that will shatter Greece. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഡൽഫിയിൽ ഏറസ് ദേവദൂതനെ നിശബ്ദമാക്കാൻ കൽപ്പിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Temple of Delphi oracle smoke vision, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Pythia speaking fate in sacred fire, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares invisible command in temple, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan warrior approaching oracle steps, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, prophecy scroll white ashes omen, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, trickery altar hidden god sigil, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, priests fleeing divine wrath, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior hesitating before sacred flame, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Greece map cracking under divine light, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, oracle eyes seeing Ghost future, hyper-detailed dramatic lighting 8k']}, {'day': 4, 'title': 'The White Ashes Curse', 'script_en': 'White ashes fall like snow over Sparta as a curse marks Kratos forever—the mark of a man the gods fear. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'വെള്ള ചാരം ശാപം പോലെ സ്പാർട്ടയിൽ വീഴുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, white ash storm over Sparta city, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, cursed warrior skin pale ash mark, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, family gravestones in ash blizzard, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares laughing through temple smoke, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, oracle curse inscription on stone, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan citizens fleeing ash rain, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior screaming at heavens, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, ash covering sun apocalyptic sky, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost mark glowing on chest, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Sparta ruins buried in white powder, hyper-detailed dramatic lighting 8k']}, {'day': 5, 'title': 'Broken Pact with Ares', 'script_en': 'The pact with Ares shatters when Kratos learns the temple fire was never accident—it was design. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഏറസുമായുള്ള കരാർ പൊളിഞ്ഞ ബലിപീഠങ്ങൾക്ക് താഴെ തകരുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, shattered altar war god statue, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos confronting Ares illusion, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, broken pact scroll burning, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan warrior renouncing chains, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine betrayal lightning strike, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, temple collapse epic destruction, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior walking away from fire shrine, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares rage colossal shadow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, white ash from broken idol, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost turning toward new quest, hyper-detailed dramatic lighting 8k']}, {'day': 6, 'title': 'Quest for Pandora Key', 'script_en': 'A whisper sends Kratos toward Pandora sealed key—a relic gods hid to cage hope itself. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'പാൻഡോറയുടെ പെട്ടകത്തിന്റെ താക്കോൽ തേടി യാത്ര ആരംഭിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, ancient vault Pandora key glowing, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior crossing desert of lost gods, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, statue of Hope chained in marble, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Greek ruins labyrinth torchlight, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, mythical key on altar of titans, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, spectral guardians blocking path, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, map to underworld gate, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior climbing crumbling temple, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Pandora silhouette in golden mist, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, key turning in divine lock, hyper-detailed dramatic lighting 8k']}, {'day': 7, 'title': 'Opening Pandora Box', 'script_en': 'Pandora box opens and plagues rush out—only one thing remains inside when the lid falls. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'പാൻഡോറയുടെ പെട്ടകം തുറന്ന് പ്രതീക്ഷ കരഞ്ഞു ചാകുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Pandora box opening dark energy, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, plagues escaping as shadow beasts, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior shielding face from winds, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Hope tiny light in box interior, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Greek city consumed by dark fog, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine chains breaking worldwide, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares watching from war throne, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, box lid slam slow motion, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior hand reaching for hope, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, apocalyptic sky over Greece, hyper-detailed dramatic lighting 8k']}, {'day': 8, 'title': 'Descent into Hades', 'script_en': 'Kratos descends the River Styx into Hades realm where dead kings remember every sin he committed. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഹേഡിസിന്റെ രാജ്യത്തിലേക്ക് ഇറങ്ങുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, River Styx boat Charon fog, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, underworld gates cerberus shadow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior descending obsidian stairs, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, souls reaching from dark water, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Hades palace green flame columns, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, judgment hall mirror of sins, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan general past self ghost, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, river of fire cavern epic scale, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, coin payment Charon close-up, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost entering realm of dead, hyper-detailed dramatic lighting 8k']}, {'day': 9, 'title': 'Slaying the God of War', 'script_en': 'On a field of divine fire, Kratos faces Ares—the master who made him monster and martyr. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ദൈവിക തീയിൽ ഏറസിനെ നേരിടുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Kratos versus Ares god of war battle, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, giant war god with flaming sword, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Blades of Chaos clashing divine steel, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Athens ruins battlefield aerial, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Pandora sword embedded in god chest, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares falling from sky epic, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior covered in god blood, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Olympus watching battle clouds, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, final strike slow motion vertical, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ares dissolving into ash, hyper-detailed dramatic lighting 8k']}, {'day': 10, 'title': 'Throne of the God of War', 'script_en': 'The throne of war stands empty until Kratos sits—and realizes power without peace is another chain. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'യുദ്ധദേവതയുടെ സിംഹാസനം പുതിയ ഉടമയെ കാത്തിരിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, God of War throne room lava rivers, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos ascending war god throne, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, crown of Ares crumbling to dust, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan warrior on divine seat, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, chains of war binding throne itself, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vision of future Norse snow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Olympus envoys bowing in fear, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, throne room statues cracking, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior rejecting crown moment, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost of Sparta new god silhouette, hyper-detailed dramatic lighting 8k']}, {'day': 11, 'title': 'Siege of Rhodes Begins', 'script_en': 'Rhodes burns beneath siege as the Colossus stirs and Zeus watches his son like a pawn on bronze. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'റോഡോസ് വെങ്കലത്തിന്റെയും രക്തത്തിന്റെയും ഉപരോധത്തിൽ കത്തുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Siege of Rhodes burning harbor, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Colossus of Rhodes awakening, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, war machines assault ancient city, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Zeus lightning over battlefield, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan warrior leading assault, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Rhodes citizens fleeing flames, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, bronze giant foot crushing ships, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, siege towers and Greek fire, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost commanding army rampart, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Mediterranean war epic vertical, hyper-detailed dramatic lighting 8k']}, {'day': 12, 'title': 'Zeus Betrayal Revealed', 'script_en': 'Zeus betrays Kratos with lightning and lies, revealing the Ghost was never champion—only sacrifice. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'സ്യൂസ് മിന്നലും വഞ്ചനയും കൊണ്ട് മകനെ വഞചിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Zeus king of gods lightning throne, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos struck by divine lightning, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, father son betrayal Olympus hall, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, scroll of hidden prophecy truth, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior falling from cloud bridge, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Athena watching in shadow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan blood on marble steps, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Zeus eyes cold divine judgment, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, broken blade after lightning, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost rising from crater, hyper-detailed dramatic lighting 8k']}, {'day': 13, 'title': 'Rise of the Titans', 'script_en': 'Ancient Titans rise from chains beneath earth to wage war on Olympus alongside the Ghost. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'പുരാതന ടൈറ്റൻമാർ ഭൂമിക്ക് താഴെ നിന്ന് ഉയരുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Titans breaking Tartarus chains, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Cronos hand emerging from ground, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos alliance with Titan general, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, earthquake splitting Mount Olympus, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, giant stone fist smashing temple, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, war council Titan and Spartan, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, chains of ages shattering, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, sky torn between Titan and god, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost before colossal Titan face, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, uprising epic vertical scale, hyper-detailed dramatic lighting 8k']}, {'day': 14, 'title': 'Gates of Mount Olympus', 'script_en': 'The gates of Mount Olympus groan open as armies of gods and titans collide in thunder. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഓളിമ്പസ് കാവൽ കതകുകൾ നിലവിളിച്ച് തുറക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Mount Olympus golden gates opening, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine army spears and shields, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Titan horde storming cloud stairs, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos leading assault on heaven, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Hermes messenger blur motion, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, cloud fortress battle panorama, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, gate inscription crumbling, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior climbing heaven steps, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, lightning storm over gates, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, epic siege vertical composition, hyper-detailed dramatic lighting 8k']}, {'day': 15, 'title': 'Athena Secret Warning', 'script_en': 'Athena warns the Ghost in a voice like falling stars: Zeus plans genocide, not redemption. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'അഥീന പ്രേതത്തെ രഹസ്യമായി മുന്നറിയിപ്പ് നൽകുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Athena goddess of wisdom apparition, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos listening on cliff night, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, owl and olive branch symbolism, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, secret map of Zeus plan, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior and goddess tense dialogue, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, stars falling prophecy sky, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, shield reflecting father face, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, temple of Athena moonlight, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost choosing vengeance path, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, whisper in storm vertical frame, hyper-detailed dramatic lighting 8k']}, {'day': 16, 'title': 'Colossus of Rhodes Falls', 'script_en': 'The Colossus of Rhodes awakens to crush invaders—and falls like prophecy of dying gods. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'റോഡോസ് കോലോസസ് ഉയർന്ന് ആക്രമികളെ ചവിട്ടുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Colossus giant striding harbor, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos inside bronze colossus mechanism, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, chain cables snapping sparks, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Rhodes statue collapsing slow motion, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, citizens running from falling giant, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior leaping between broken limbs, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, bronze head crashing into sea, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, siege victory ash and rubble, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost standing on fallen god-metal, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, epic destruction vertical 9:16, hyper-detailed dramatic lighting 8k']}, {'day': 17, 'title': 'Climbing Mount Olympus', 'script_en': 'Kratos climbs Mount Olympus step by bloody step while gods hurl miracles like stones. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഓളിമ്പസ് മലം ചെയ്യിക്കയറുന്നു, ഓരോ പടിയും രക്തം. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, warrior climbing cloud mountain stairs, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, blood trail on marble steps, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Poseidon wave crashing cliff, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine obstacles fire ice wind, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos wounded ascending still, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, eagles of Zeus attacking, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, chain hook into heaven rock, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost silhouette against sun, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, summit lightning above, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical climb epic perspective, hyper-detailed dramatic lighting 8k']}, {'day': 18, 'title': 'Wrath of Poseidon', 'script_en': 'Poseidon rises with tsunamis against the Ghost, seas obeying the rage of a dying pantheon. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'പോസിഡോൺ സുനാമികൾ പ്രേതത്തിനെതിരെ ഉയർത്തുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Poseidon god of sea trident storm, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, massive tsunami over Greek coast, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos underwater battle ruins, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, hippocampus sea beasts attack, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, trident vs blades clash, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, ship graveyard epic waves, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior surfacing from abyss, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, ocean splitting divine power, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost drenched in sea foam, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical ocean apocalypse scene, hyper-detailed dramatic lighting 8k']}, {'day': 19, 'title': 'Hades Final Trial', 'script_en': 'Hades sets a final trial in underworld depths—face every ghost Kratos created or stay dead. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഹേഡിസ് അന്തിമ പരീക്ഷ പാതാളത്തിൽ വെക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Hades god underworld green fire, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, trial arena souls as audience, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior facing mirror sins army, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, river Lethe mist labyrinth, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan ghosts accusing, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, judge throne skull ornament, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos breaking soul chains, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, underworld collapsing ceiling, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, final trial door opening light, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical hell courtroom epic, hyper-detailed dramatic lighting 8k']}, {'day': 20, 'title': 'Chariot of Helios', 'script_en': 'The chariot of Helios blazes across sky as Kratos pulls the sun toward eternal dusk. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഹീലിയോസിന്റെ രഥം ആകാശം കത്തിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Helios sun god golden chariot, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos grappling flaming horses, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, sky burning day to night, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Icarus wings ash falling metaphor, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, solar flare over Olympus, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior riding chariot chaos, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, chariot wheel breaking sparks, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, world darkening epic horizon, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost against blinding sun, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical solar fall composition, hyper-detailed dramatic lighting 8k']}, {'day': 21, 'title': 'Speed of Hermes', 'script_en': 'Hermes moves faster than vengeance until Kratos learns to strike where speed cannot run. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഹർമീസ് പ്രതികാരത്തേക്കാൾ വേഗത്തിൽ നീങ്ങുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Hermes winged sandals blur trail, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, high speed combat cloud city, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos anticipating divine dash, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, caduceus staff parry sparks, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, time slow motion blade catch, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, messenger god smirking fall, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, feathers and blood midair, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Olympus corridor chase scene, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost victorious over speed, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical motion blur myth fight, hyper-detailed dramatic lighting 8k']}, {'day': 22, 'title': 'Titans Break Their Prison', 'script_en': 'Titans break their prison and shake the world as Olympus cracks like brittle marble. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ടൈറ്റൻമാർ തങ്ങളുടെ കാവൽ തകർത്ത് ലോകം കുലുക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Titan prison chains exploding, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, world map cracking tectonic, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Olympus palace splitting, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos amid falling heaven, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Atlas holding sky trembling, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, earth lava rivers opening, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, gods fleeing cloud citadel, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost orchestrating collapse, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, sky raining stone temples, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical world end panorama, hyper-detailed dramatic lighting 8k']}, {'day': 23, 'title': 'Zeus Lightning Judgment', 'script_en': 'Zeus judgment falls as lightning without mercy—a father trying to erase his mistake. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'സ്യൂസിന്റെ മിന്നൽ വിധി കരുണയില്ലാതെ വീഴുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Zeus storm throne lightning bolts, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos deflecting thunder shield, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, father son duel cloud peak, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, lightning scarring land below, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine law hammer gavel imagery, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior rage against sky king, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Olympus trembling under strikes, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, broken storm clouds vortex, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost roaring at heaven, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical lightning battle epic, hyper-detailed dramatic lighting 8k']}, {'day': 24, 'title': 'Olympus Crumbles', 'script_en': 'Mount Olympus crumbles stone by stone as the age of Greek gods ends in dust and silence. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഓളിമ്പസ് കല്ല് കallaxായി ചെല്ലിമരിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Mount Olympus collapsing aerial, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, marble columns falling slow motion, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, statues of gods shattering, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos walking through ruin rain, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, clouds dissipating empty throne, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, eagles fleeing broken nest, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, divine light fading horizon, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost amid rubble summit, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, last temple bell ringing, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical godfall composition, hyper-detailed dramatic lighting 8k']}, {'day': 25, 'title': 'Final Showdown with Zeus', 'script_en': 'The final battle with Zeus begins at the summit where family, fate, and fury collide. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'സ്യൂസുമായുള്ള അന്തിമ പോരാട്ടം ശിഖരത്തിൽ. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Zeus vs Kratos summit duel, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Blade of Olympus glowing, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, father thrown through clouds, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Gaia earth hand intervention, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, lightning and blood storm, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, warrior strangling god king, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, time freeze emotional flashback, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Zeus falling from heaven, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost standing alone peak, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical final boss cinematic, hyper-detailed dramatic lighting 8k']}, {'day': 26, 'title': 'Exodus from Greece', 'script_en': 'Kratos turns his back on Greece forever, leaving ruins where gods once claimed immortality. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'ഗ്രീസിനോട് വിട പറഞ്ഞ് ഇനി ഒരിക്കലും തിരിയാതെ നീങ്ങുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, warrior leaving burned Greece coast, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, longboat on dark sea departure, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Sparta ash horizon goodbye, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, map fading Greece to north, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, family urn in hands, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, sunset over ruined temples, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost silhouette on ship bow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, oars cutting silent water, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, seagulls over dead gods land, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical exodus poster frame, hyper-detailed dramatic lighting 8k']}, {'day': 27, 'title': 'Northern Seas Exile', 'script_en': 'Northern seas swallow the exile longboat as winter teaches silence after thunder. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'വടക്കൻ കടലുകൾ നിഷ്പല്പനത്തിന്റെ പടക്കപ്പുലി വിഴുങ്ങുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Viking longboat icy northern sea, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos hooded at prow snow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, whale and iceberg mythic sea, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, storm waves freezing spray, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, exile family under fur cloak, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, constellation norse stars, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Greek armor buried under deck, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost becoming legend north, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, fog bank mysterious shore, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical cold ocean journey, hyper-detailed dramatic lighting 8k']}, {'day': 28, 'title': 'Midgard Dark Forests', 'script_en': 'Midgard dark forests whisper of new gods as Kratos enters a realm that does not know his name. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'മിഡ്ഗാർഡിന്റെ ഇരുണ്ട കാടുകൾ പുതിയ ദേവന്മാരെ മന്ത്രിക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Norse dark forest snow pines, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Kratos entering Midgard path, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, rune stones glowing faint, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Atreus child silhouette distant, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, wolf eyes in underbrush, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, frozen river crossing, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, old Greek blade wrapped cloth, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost reduced to man again, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Norse shrine in mist, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical forest myth atmosphere, hyper-detailed dramatic lighting 8k']}, {'day': 29, 'title': 'Blades Buried in Earth', 'script_en': 'The blades are buried deep in frozen earth—a vow that war ends with the last god fallen. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'വാളുകൾ മരഞ്ഞ ഭൂമിയിൽ കുഴിച്ചിടുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Kratos burying blades in snow, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, axe and sword in stone grave, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Atreus watching solemn, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, hand over buried weapon ritual, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Norse cabin smoke background, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, blood washed from hands stream, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost becoming father not god, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, runes carved on burial stone, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, wind over empty battlefield past, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical burial vow composition, hyper-detailed dramatic lighting 8k']}, {'day': 30, 'title': 'Shield of Atreus', 'script_en': 'Atreus stands behind his father shield as Kratos chooses protection over destruction. Once he served Ares without question; now every scar maps a rebellion he refused to name. Lydia and Calliope live only in memory, yet their voices echo whenever steel grows heavy. Zeus promised redemption without cost. Ares promised victory without end. Both lied. The Ghost does not pray—he plans. Allies whisper. Enemies scream. Olympus watches from lightning-scented clouds. Each conquest became a debt the gods never intended to pay. Sparta did not raise a saint; it raised a weapon that learned to think. Earth trembles beneath footsteps that no longer kneel. This saga demands reckoning, not forgiveness. Tonight Greece—or the world beyond—will never be the same. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken. The gods believe fear can chain him forever. They forget what he became when family was taken.', 'script_ml': 'അട്രിയസ് പിതാവിന്റെ കാവലിന് പിന്നിൽ നിൽക്കുന്നു. ഏറസിന് വേണ്ടി അയാൾ ഒരിക്കൽ ചോദ്യം ചെയ്യാതെ സേവിച്ചു; ഇപ്പോൾ ഓരോ പാടും പേരുപറയാത്ത പ്രതിരോധത്തിന്റെ വരയം. ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ മാത്രം, എന്നാലും വാൾ കനത്താൽ അവരുടെ ശബ്ദം തിരിച്ചുവരുന്നു. സ്യൂസ് വിലയില്ലാത്ത രക്ഷ വാഗ്ദാനം ചെയ്തു; ഏറസ് അവസാനിക്കാത്ത വിജയം. ഇരുവരും നുണ പറഞ്ഞു. കരാതോസ പ്രാർത്ഥിക്കില്ല—പദ്ധതി തയ്യാറാക്കുന്നു. ഒത്തുക്കൾ മന്ത്രിക്കുന്നു; ശത്രുക്കൾ നിലവിളിക്കുന്നു. ഓളിമ്പസ് മിന്നൽ മേഘങ്ങളിൽ നോക്കുന്നു. ഓരോ വിജയവും ദേവന്മാർ തിരിച്ചുനൽകാൻ ഉദ്ദേശിച്ചില്ലാത്ത കടം. സ്പാർട്ട ഒരു ശുദ്ധനെ ഉയർത്തിയില്ല; ചിന്തിക്കാൻ പഠിച്ച ആയുധം. മുട്ടുകുത്താൻ നിർത്തിയ കാൽനടയിൽ ഭൂമി കുലുക്കുന്നു. ഈ കഥ ക്ഷമ ചോദിക്കില്ല—പ്രതിക്ഷയം ആവശ്യപ്പെടുന്നു. ഇന്ന് ഗ്രീസ്—അതോ അതിനപ്പുറുള്ള ലോകം—ഒരിക്കലും പഴയതല്ല. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു. ദേവന്മാർ ഭയം അയാളെ ചങ്ങലയിടുമെന്ന് വിശ്വസിക്കുന്നു. കുടുംബം പിരിച്ചെടുത്തപ്പോൾ അവർ വിധി എന്ന് പറഞ്ഞത് മറക്കുന്നു.', 'prompts': ['Cinematic vertical 9:16 epic mythological dark fantasy, Kratos shielding Atreus from danger, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, father and son norse cabin fire, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, teaching bow not blade, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Ghost becoming guardian, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, snowfall peaceful night, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, Spartan mark hidden under cloak, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, new saga dawn horizon, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, hands on boy shoulders, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, two wolves shadow loyalty, hyper-detailed dramatic lighting 8k', 'Cinematic vertical 9:16 epic mythological dark fantasy, vertical legacy ending frame, hyper-detailed dramatic lighting 8k']}]



# ---------------------------------------------------------------------------
# Episode loading (trailer JSON + embedded fallback)
# ---------------------------------------------------------------------------

SPEAKER_TAG_PATTERN = re.compile(r"\[[^\]]+\]:\s*")
DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097f]")
MOTION_CYCLE = ("zoom_in", "pan_right", "zoom_out", "pan_left", "whip_pan", "tracking_shot")
PACE_DURATIONS = {"action": (2.0, 3.0), "establishing": (4.0, 5.0), "hook": (3.0, 5.0)}
MOTION_ALIASES = {
    "in": "zoom_in",
    "zoom_in": "zoom_in",
    "zoomin": "zoom_in",
    "out": "zoom_out",
    "zoom_out": "zoom_out",
    "zoomout": "zoom_out",
    "pan_left": "pan_left",
    "pan_right": "pan_right",
    "whip_pan": "whip_pan",
    "whip": "whip_pan",
    "tracking_shot": "tracking_shot",
    "tracking": "tracking_shot",
}
ACTION_PROMPT_KEYWORDS = (
    "slow motion",
    "mid swing",
    "slamming",
    "explosion",
    "leaping",
    "cutting through",
    "shockwave",
    "climax",
    "battle cry",
    "impact",
    "strike",
    "fight",
    "combat",
)
SUB_SHOT_PROMPT_SUFFIXES = (
    "extreme close-up cinematic shot, shallow depth of field, ",
    "wide-angle dynamic cinematic shot, epic scale, ",
    "over-the-shoulder action reaction shot, motion blur, ",
    "low angle heroic impact shot, dramatic perspective, ",
)


def normalize_motion(motion: str) -> str:
    key = motion.lower().replace("-", "_").strip()
    return MOTION_ALIASES.get(key, "zoom_in")


def _eased_progress(linear: float, motion: str) -> float:
    linear = min(max(linear, 0.0), 1.0)
    if motion == "whip_pan":
        return 1.0 - (1.0 - min(linear * 1.75, 1.0)) ** 4
    if motion == "tracking_shot":
        return linear * linear * (3.0 - 2.0 * linear)
    if motion in ("zoom_in", "zoom_out"):
        return linear * linear * (3.0 - 2.0 * linear)
    return linear


def _is_rapid_dialogue(scene: dict[str, Any]) -> bool:
    combined = f"{scene.get('voiceover_en', '')} {scene.get('voiceover_ml', '')}"
    speaker_count = combined.count("[")
    return speaker_count >= 3 or (scene.get("type") == "action" and speaker_count >= 2)


def _is_complex_action_scene(scene: dict[str, Any]) -> bool:
    if scene.get("complex_action") or scene.get("force_sub_shots"):
        return True
    if _is_rapid_dialogue(scene):
        return True
    prompt = scene.get("prompt", "").lower()
    if scene.get("type") == "action" and any(k in prompt for k in ACTION_PROMPT_KEYWORDS):
        return True
    voice = f"{scene.get('voiceover_en', '')} {scene.get('voiceover_ml', '')}".lower()
    return scene.get("type") == "action" and voice.count("[") >= 2


def _sub_shot_count(scene: dict[str, Any], audio_duration: float) -> int:
    if scene.get("sub_shots"):
        return max(len(scene["sub_shots"]), 1)
    needs_split = (
        audio_duration > SUB_SHOT_THRESHOLD_SEC
        or _is_complex_action_scene(scene)
        or _is_rapid_dialogue(scene)
    )
    if not needs_split:
        return 1
    if scene.get("type") == "action" or _is_complex_action_scene(scene):
        if audio_duration > 9.0:
            return 4
        if audio_duration > 6.0:
            return 3
        return SUB_SHOT_MIN
    if audio_duration > 5.5:
        return 3
    return SUB_SHOT_MIN


def _build_sub_shot_specs(scene: dict[str, Any], count: int) -> list[dict[str, str]]:
    if scene.get("sub_shots"):
        specs = []
        for i, shot in enumerate(scene["sub_shots"][:count]):
            specs.append(
                {
                    "prompt": shot["prompt"],
                    "motion": normalize_motion(shot.get("motion", scene.get("motion", "zoom_in"))),
                }
            )
        return specs

    base_motion = normalize_motion(scene.get("motion", "zoom_in"))
    pool = (
        ["whip_pan", "zoom_in", "zoom_out", "pan_left", "pan_right", "whip_pan"]
        if scene.get("type") == "action"
        else ["tracking_shot", "pan_right", "pan_left", "zoom_in", "tracking_shot", "zoom_out"]
    )
    specs: list[dict[str, str]] = []
    for i in range(count):
        suffix = SUB_SHOT_PROMPT_SUFFIXES[i % len(SUB_SHOT_PROMPT_SUFFIXES)]
        specs.append(
            {
                "prompt": f"{suffix}{scene['prompt']}",
                "motion": normalize_motion(scene["sub_shot_motions"][i])
                if scene.get("sub_shot_motions") and i < len(scene["sub_shot_motions"])
                else pool[i % len(pool)]
                if scene.get("type") == "action" or i > 0
                else base_motion,
            }
        )
    return specs


def get_audio_duration(audio_path: Path) -> float:
    clip = AudioFileClip(str(audio_path))
    try:
        return float(clip.duration)
    finally:
        clip.close()


ML_PHRASE_FIXES: dict[str, str] = {
    "നിർമ്മിക്കപ്പെട്ടു": "കെട്ടിപ്പണിത്തു",
    "സൃഷ്ടിച്ചു കൊണ്ടിരിക്കുകയാണെന്ന്": "സൃഷ്ടിച്ചുകൊണ്ടിരിക്കുകയായിരുന്നു",
    "പിതാവ് ആയുധത്തേക്കാൾ മുകളിൽ": "പിതാവ് ആയുധത്തിന് മുകളിലേക്ക് ഉയർന്നു",
    "പിതാവ് ആയുധത്തിന് മുകളിൽ": "പിതാവ് ആയുധത്തിന് മുകളിലേക്ക് ഉയർന്നു",
    "മുദ്രപ്പെട്ടു": "അടയാളപ്പെടുത്തി",
    "എന്റെ മകനായി": "എന്റെ മകനു വേണ്ടി",
    "ഹൃദയം സ്ഥിരം": "മനസ് സ്ഥിരമായി",
    "എല്ലാം കഴിഞ്ഞോ": "എല്ലാം കഴിഞ്ഞോ അച്ഛാ",
    "ക്രatosസ്": "ക്രാറ്റോസ്",
    "സ്വീകരിച്ചിരിക്കുന്നു": "സ്വീകരിച്ചു",
    "നിലനിർത്താതെ ഇടിച്ചു തകർ": "നിൽക്കാതെ എല്ലാം തകർക്ക്",
}


def prepare_english_tts(text: str) -> str:
    """Strip speaker tags for English narration."""
    cleaned = SPEAKER_TAG_PATTERN.sub("", text)
    cleaned = DEVANAGARI_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("'", "").replace('"', "")
    return " ".join(cleaned.split())


def prepare_malayalam_tts(text: str) -> str:
    """Clean Malayalam for natural Edge-TTS — preserve pauses, fix literal phrasing."""
    cleaned = SPEAKER_TAG_PATTERN.sub("", text)
    cleaned = DEVANAGARI_PATTERN.sub("", cleaned)
    for bad, good in ML_PHRASE_FIXES.items():
        cleaned = cleaned.replace(bad, good)
    cleaned = cleaned.replace("...", " ... ")
    cleaned = cleaned.replace("!", "! ")
    cleaned = cleaned.replace("?", "? ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if DEVANAGARI_PATTERN.search(cleaned):
        log.warning("Malayalam TTS text still contains stray Devanagari characters.")
    return cleaned


def prepare_tts_text(text: str, lang: str = "en") -> str:
    if lang == "ml":
        return prepare_malayalam_tts(text)
    return prepare_english_tts(text)


def _clamp_bgm_volume(value: float | None) -> float:
    if value is None:
        return DEFAULT_BGM_VOLUME
    return max(BGM_VOLUME_MIN, min(BGM_VOLUME_MAX, float(value)))


def resolve_bgm_cue(scene: dict[str, Any]) -> str:
    explicit = scene.get("bgm_cue")
    if explicit and explicit in BGM_CUE_CONFIG:
        return explicit
    return BGM_CUE_BY_SCENE_TYPE.get(scene.get("type", ""), "default")


def _normalize_scene(raw_scene: dict[str, Any], episode: dict[str, Any], index: int) -> dict[str, Any]:
    scene_id = int(raw_scene["id"])
    scene_type = raw_scene.get("type", "action")
    raw_motion = raw_scene.get("motion") or MOTION_CYCLE[(scene_id - 1) % len(MOTION_CYCLE)]
    motion = normalize_motion(raw_motion)
    hook_en = episode.get("hook_en", "")
    hook_ml = episode.get("hook_ml", "")
    return {
        "id": scene_id,
        "type": scene_type,
        "pace": raw_scene.get("pace", "establishing" if scene_type == "establishing" else "action"),
        "motion": motion,
        "bgm_cue": resolve_bgm_cue(raw_scene),
        "bgm_intensity": float(raw_scene.get("bgm_intensity", 1.0)),
        "prompt": raw_scene["prompt"],
        "voiceover_en": raw_scene.get("voiceover_en", ""),
        "voiceover_ml": raw_scene.get("voiceover_ml", ""),
        "title_overlay_en": raw_scene.get("title_overlay_en") or (hook_en if scene_type == "hook" else ""),
        "title_overlay_ml": raw_scene.get("title_overlay_ml") or (hook_ml if scene_type == "hook" else ""),
        "complex_action": bool(raw_scene.get("complex_action", False)),
        "force_sub_shots": bool(raw_scene.get("force_sub_shots", False)),
        "sub_shots": raw_scene.get("sub_shots"),
        "sub_shot_motions": raw_scene.get("sub_shot_motions"),
    }


def normalize_episode(raw: dict[str, Any], source: str) -> dict[str, Any]:
    day = int(raw["day"])
    bgm_volume = _clamp_bgm_volume(raw.get("bgm_volume"))

    if "scenes" in raw:
        scenes = sorted(raw["scenes"], key=lambda x: int(x["id"]))
        if not SCENE_COUNT_MIN <= len(scenes) <= SCENE_COUNT_MAX:
            raise ValueError(
                f"Day {day} needs {SCENE_COUNT_MIN}-{SCENE_COUNT_MAX} scenes, got {len(scenes)}"
            )
        normalized_scenes = [_normalize_scene(scene, raw, i) for i, scene in enumerate(scenes)]
        return {
            "day": day,
            "title": raw.get("title_en", raw.get("title", f"Day {day}")),
            "title_ml": raw.get("title_ml", raw.get("title_en", f"Day {day}")),
            "hook_en": raw.get("hook_en", ""),
            "hook_ml": raw.get("hook_ml", ""),
            "scenes": normalized_scenes,
            "prompts": [scene["prompt"] for scene in normalized_scenes],
            "bgm_volume": bgm_volume,
            "source": source,
            "format": "cinematic_scenes",
        }

    prompts = raw.get("prompts", [])
    if len(prompts) < IMAGE_COUNT:
        raise ValueError(f"Day {day} needs {IMAGE_COUNT} prompts, got {len(prompts)}")

    if "title_en" in raw:
        script_en = raw["script_en"]
        script_ml = raw["script_ml"]
        if raw.get("hook_en"):
            script_en = f"{raw['hook_en']} ... {script_en}"
        if raw.get("hook_ml"):
            script_ml = f"{raw['hook_ml']} ... {script_ml}"
        return {
            "day": day,
            "title": raw["title_en"],
            "title_ml": raw.get("title_ml", raw["title_en"]),
            "script_en": prepare_english_tts(script_en),
            "script_ml": prepare_malayalam_tts(script_ml),
            "prompts": prompts,
            "bgm_volume": bgm_volume,
            "source": source,
            "format": "legacy_flat",
        }

    return {
        "day": day,
        "title": raw.get("title", f"Day {day}"),
        "title_ml": raw.get("title_ml", raw.get("title", f"Day {day}")),
        "script_en": prepare_english_tts(raw["script_en"]),
        "script_ml": prepare_malayalam_tts(raw["script_ml"]),
        "prompts": prompts,
        "bgm_volume": bgm_volume,
        "source": source,
        "format": "legacy_flat",
    }


def script_json_path(day: int) -> Path:
    return SCRIPTS_DIR / f"day_{day:02d}_script.json"


def load_script_json(day: int) -> dict[str, Any] | None:
    path = script_json_path(day)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    log.info("Loaded trailer script: %s", path.name)
    return data


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open(encoding="utf-8") as fh:
                data = json.load(fh)
            day = int(data.get("current_day", 1))
            return {"current_day": max(1, min(day, 30))}
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            log.warning("Invalid state.json (%s); resetting to day 1.", exc)
    return {"current_day": 1}


def save_state(state: dict[str, Any]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def get_episode(day: int) -> dict[str, Any]:
    raw = load_script_json(day)
    if raw is not None:
        return normalize_episode(raw, "trailer_json")
    for ep in EPISODE_DATA:
        if ep["day"] == day:
            return normalize_episode(ep, "embedded")
    raise ValueError(f"No episode found for day {day}")


# ---------------------------------------------------------------------------
# Audio (Edge-TTS)
# ---------------------------------------------------------------------------


async def generate_audio(
    text: str,
    voice: str,
    output_path: Path,
    rate: str | None = None,
    pitch: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, str] = {}
    if rate:
        kwargs["rate"] = rate
    if pitch:
        kwargs["pitch"] = pitch
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    await communicate.save(str(output_path))
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Audio generation failed: {output_path}")
    log.info("Audio saved: %s", output_path.name)
    return output_path


def _tts_profile(lang: str) -> tuple[str | None, str | None]:
    if lang == "ml":
        return VOICE_ML_RATE, VOICE_ML_PITCH
    if lang == "en":
        return VOICE_EN_RATE, VOICE_EN_PITCH
    return None, None


async def generate_scene_audios(
    scenes: list[dict[str, Any]], voice: str, lang: str, day: int
) -> list[Path]:
    """Generate one Edge-TTS clip per scene; duration drives the matching visual cut."""
    paths: list[Path] = []
    rate, pitch = _tts_profile(lang)
    for scene in scenes:
        raw_text = scene[f"voiceover_{lang}"]
        text = prepare_tts_text(raw_text, lang=lang)
        if not text:
            raise ValueError(f"Day {day} scene {scene['id']} missing voiceover_{lang}")
        dest = TEMP_AUDIO_DIR / f"day{day:02d}_{lang}_scene{scene['id']:02d}.mp3"
        await generate_audio(text, voice, dest, rate=rate, pitch=pitch)
        paths.append(dest)
    return paths


def concatenate_audio_files(audio_paths: list[Path], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    clips = [AudioFileClip(str(path)) for path in audio_paths]
    try:
        combined = concatenate_audioclips(clips)
        write_kwargs: dict[str, Any] = {"logger": None}
        try:
            combined.write_audiofile(str(output_path), **write_kwargs)
        except TypeError:
            write_kwargs.pop("logger", None)
            combined.write_audiofile(str(output_path), **write_kwargs)
        combined.close()
    finally:
        for clip in clips:
            clip.close()
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Audio concatenation failed: {output_path}")
    log.info("Concatenated narration: %s", output_path.name)
    return output_path


def ensure_bgm_library() -> dict[str, Path]:
    """Download/load distinct BGM cues for atmospheric, combat, and hook scenes."""
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    library: dict[str, Path] = {}

    for cue, config in BGM_CUE_CONFIG.items():
        dest = BGM_DIR / config["file"]
        if dest.exists() and dest.stat().st_size > 10_000:
            library[cue] = dest
            continue

        for url in config["urls"]:
            try:
                log.info("Downloading BGM cue '%s': %s", cue, url)
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                dest.write_bytes(response.content)
                if dest.stat().st_size > 10_000:
                    library[cue] = dest
                    log.info("BGM cue saved: %s", dest.name)
                    break
            except (OSError, requests.RequestException) as exc:
                log.warning("BGM cue '%s' download failed (%s): %s", cue, url, exc)

    if "default" not in library and BGM_PATH.exists() and BGM_PATH.stat().st_size > 10_000:
        library["default"] = BGM_PATH

    if not library:
        log.warning("No BGM cues available; exporting voice-only audio.")
    else:
        log.info("BGM library ready: %s", ", ".join(sorted(library)))
    return library


def ensure_bgm() -> Path | None:
    """Legacy helper — returns the default BGM track."""
    library = ensure_bgm_library()
    return library.get("default") or library.get("epic_hook") or next(iter(library.values()), None)


def _loop_segment_from_offset(source, duration: float, start_offset: float):
    if start_offset > 0 and source.duration > start_offset:
        base = _clip_subclip(source, start_offset, source.duration)
    else:
        base = source
    if base.duration >= duration:
        trimmed = _clip_subclip(base, 0, duration)
        if base is not source:
            base.close()
        return trimmed
    looped = _loop_audio_to_duration(base, duration)
    if base is not source:
        base.close()
    return looped


def _extract_bgm_shot_segment(
    bgm_path: Path,
    duration: float,
    cue: str,
    shot_index: int,
    volume_scale: float = 1.0,
):
    """Slice BGM so beat-drop offsets align with sub-shot / scene cuts."""
    config = BGM_CUE_CONFIG[cue]
    source = AudioFileClip(str(bgm_path))
    restart = config.get("restart_per_shot", False) or shot_index == 0
    start_offset = config["beat_offset"] if restart else 0.0
    segment = _loop_segment_from_offset(source, duration, start_offset)
    volume = _clamp_bgm_volume(config["volume"] * volume_scale)
    quiet = _audio_volumex(segment, volume)
    if segment is not source:
        segment.close()
    source.close()
    return quiet


def build_shot_aligned_bgm_track(
    shots: list[dict[str, Any]],
    cue: str,
    bgm_path: Path,
    volume_scale: float = 1.0,
):
    """Concatenate per-shot BGM slices so musical hits land on each visual cut."""
    if not shots:
        raise ValueError("Cannot build BGM track without shots")
    if len(shots) == 1:
        return _extract_bgm_shot_segment(bgm_path, shots[0]["duration"], cue, 0, volume_scale)

    segments = [
        _extract_bgm_shot_segment(bgm_path, shot["duration"], cue, idx, volume_scale)
        for idx, shot in enumerate(shots)
    ]
    combined = concatenate_audioclips(segments)
    for seg in segments:
        seg.close()
    return combined


def mix_scene_voice_with_cue_bgm(
    voice_clip,
    shots: list[dict[str, Any]],
    scene: dict[str, Any],
    bgm_library: dict[str, Path],
    volume_scale: float = 1.0,
):
    """Mix narration with scene-specific BGM aligned to sub-shot cuts."""
    cue = resolve_bgm_cue(scene)
    bgm_path = bgm_library.get(cue) or bgm_library.get("default")
    if bgm_path is None or not bgm_path.exists():
        return voice_clip, None

    intensity = float(scene.get("bgm_intensity", 1.0)) * volume_scale
    bgm_track = build_shot_aligned_bgm_track(shots, cue, bgm_path, intensity)
    mixed = CompositeAudioClip([bgm_track, voice_clip])
    log.info(
        "Scene %s BGM cue '%s' (%s shot slice(s), intensity %.2f)",
        scene["id"],
        cue,
        len(shots),
        intensity,
    )
    return mixed, bgm_track


def _clip_subclip(clip, start: float, end: float):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)


def _audio_volumex(clip, factor: float):
    if hasattr(clip, "with_volume_scaled"):
        return clip.with_volume_scaled(factor)
    if hasattr(clip, "volumex"):
        return clip.volumex(factor)
    return clip


def _loop_audio_to_duration(clip, target_duration: float):
    if clip.duration >= target_duration:
        return _clip_subclip(clip, 0, target_duration)
    parts = []
    total = 0.0
    while total < target_duration:
        parts.append(clip)
        total += clip.duration
    looped = concatenate_audioclips(parts)
    trimmed = _clip_subclip(looped, 0, target_duration)
    looped.close()
    return trimmed


def mix_voice_with_bgm(voice_path: Path, bgm_path: Path | None, bgm_volume: float) -> Path:
    """Mix narration with epic BGM underneath for maximum retention."""
    if bgm_path is None or not bgm_path.exists():
        return voice_path

    mixed_path = voice_path.with_name(voice_path.stem + "_mixed.mp3")
    voice = AudioFileClip(str(voice_path))
    bgm = AudioFileClip(str(bgm_path))
    try:
        bgm_looped = _loop_audio_to_duration(bgm, voice.duration)
        bgm_quiet = _audio_volumex(bgm_looped, bgm_volume)
        mixed = CompositeAudioClip([bgm_quiet, voice])
        write_kwargs: dict[str, Any] = {"logger": None}
        try:
            mixed.write_audiofile(str(mixed_path), **write_kwargs)
        except TypeError:
            write_kwargs.pop("logger", None)
            mixed.write_audiofile(str(mixed_path), **write_kwargs)
        mixed.close()
        if bgm_looped is not bgm:
            bgm_looped.close()
    finally:
        voice.close()
        bgm.close()

    if not mixed_path.exists() or mixed_path.stat().st_size == 0:
        raise RuntimeError(f"BGM mix failed: {mixed_path}")
    log.info("Mixed BGM at %.2f under narration: %s", bgm_volume, mixed_path.name)
    return mixed_path


# ---------------------------------------------------------------------------
# Images (Pollinations.ai)
# ---------------------------------------------------------------------------


def pollinations_url(prompt: str) -> str:
    encoded = urllib.parse.quote(prompt, safe="")
    return POLLINATIONS_BASE.format(prompt=encoded, w=VIDEO_WIDTH, h=VIDEO_HEIGHT)


def download_image(prompt: str, dest: Path, index: int, day: int, total: int | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = pollinations_url(prompt)
    last_error: Exception | None = None
    label_total = total or IMAGE_COUNT

    for attempt in range(1, IMAGE_DOWNLOAD_RETRIES + 1):
        try:
            log.info("Day %s image %s/%s (attempt %s)", day, index, label_total, attempt)
            resp = requests.get(url, timeout=IMAGE_DOWNLOAD_TIMEOUT, stream=True)
            resp.raise_for_status()
            tmp = dest.with_suffix(".part")
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
            for move_attempt in range(1, 4):
                try:
                    tmp.replace(dest)
                    break
                except OSError as exc:
                    if move_attempt == 3:
                        raise
                    log.warning("Could not finalize %s (%s); retrying...", dest.name, exc)
                    time.sleep(1 * move_attempt)
            with Image.open(dest) as img:
                img.verify()
            log.info("Downloaded: %s", dest.name)
            time.sleep(REQUEST_DELAY_SEC)
            return dest
        except Exception as exc:
            last_error = exc
            log.warning("Image download failed (%s); retrying...", exc)
            time.sleep(2 * attempt)

    raise RuntimeError(f"Failed to download image after {IMAGE_DOWNLOAD_RETRIES} attempts: {last_error}")


def plan_and_download_scene_shots(
    day: int, scenes: list[dict[str, Any]], scene_audio_paths: list[Path]
) -> list[dict[str, Any]]:
    """Build sub-shot plans from scene audio duration and download Pollinations variations."""
    shot_plans: list[dict[str, Any]] = []
    total_images = sum(
        _sub_shot_count(scene, get_audio_duration(path))
        for scene, path in zip(scenes, scene_audio_paths)
    )
    image_index = 0
    for scene, audio_path in zip(scenes, scene_audio_paths):
        duration = get_audio_duration(audio_path)
        count = _sub_shot_count(scene, duration)
        specs = _build_sub_shot_specs(scene, count)
        shot_duration = duration / count
        shots: list[dict[str, Any]] = []

        log.info(
            "Scene %s: %.1fs narration -> %s sub-shot(s) (~%.1fs each)",
            scene["id"],
            duration,
            count,
            shot_duration,
        )

        for shot_idx, spec in enumerate(specs, start=1):
            image_index += 1
            dest = TEMP_IMAGES_DIR / f"day{day:02d}_scene{scene['id']:02d}_shot{shot_idx:02d}.jpg"
            download_image(spec["prompt"], dest, image_index, day, total=total_images)
            shots.append(
                {
                    "img_path": dest,
                    "duration": shot_duration,
                    "motion": spec["motion"],
                }
            )

        shot_plans.append(
            {
                "scene": scene,
                "shots": shots,
                "total_duration": duration,
            }
        )
    return shot_plans


def download_episode_images(episode: dict[str, Any]) -> list[Path]:
    day = episode["day"]
    prompts = episode["prompts"]
    expected = len(episode["scenes"]) if episode.get("format") == "cinematic_scenes" else IMAGE_COUNT
    if len(prompts) < expected:
        raise ValueError(f"Day {day} needs {expected} prompts, got {len(prompts)}")

    paths: list[Path] = []
    for i, prompt in enumerate(prompts, start=1):
        dest = TEMP_IMAGES_DIR / f"day{day:02d}_img{i:02d}.jpg"
        paths.append(download_image(prompt, dest, i, day))
    return paths


# ---------------------------------------------------------------------------
# Video assembly (MoviePy)
# ---------------------------------------------------------------------------


def _clip_set_duration(clip, duration: float):
    if hasattr(clip, "with_duration"):
        return clip.with_duration(duration)
    return clip.set_duration(duration)


def _clip_set_audio(clip, audio):
    if hasattr(clip, "with_audio"):
        return clip.with_audio(audio)
    return clip.set_audio(audio)


def _clip_resize(clip, size: tuple[int, int]):
    if hasattr(clip, "resized"):
        return clip.resized(size)
    return clip.resize(size)


def get_scene_subtitle(scene: dict[str, Any], lang: str) -> str:
    raw = scene.get(f"voiceover_{lang}", "")
    return prepare_tts_text(raw, lang=lang)


def _format_srt_timestamp(seconds: float) -> str:
    ms = int(round(max(seconds, 0.0) * 1000))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_scene_srt(
    scene_shot_plans: list[dict[str, Any]],
    scene_audio_paths: list[Path],
    output_path: Path,
    lang: str,
) -> Path:
    """Export sidecar SRT subtitles synced to scene narration."""
    srt_path = output_path.with_suffix(f".{lang}.srt")
    lines: list[str] = []
    cursor = 0.0
    for index, (plan, audio_path) in enumerate(zip(scene_shot_plans, scene_audio_paths), start=1):
        duration = get_audio_duration(audio_path)
        text = get_scene_subtitle(plan["scene"], lang)
        if not text:
            cursor += duration
            continue
        start = cursor
        end = cursor + duration
        lines.append(str(index))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
        cursor = end
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Subtitles saved: %s", srt_path.name)
    return srt_path


def _first_hook_scene(scenes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for scene in scenes:
        if scene.get("type") == "hook":
            return scene
    return scenes[0] if scenes else None


def _last_hook_scene(scenes: list[dict[str, Any]]) -> dict[str, Any] | None:
    hooks = [s for s in scenes if s.get("type") == "hook"]
    return hooks[-1] if hooks else None


def _build_thumbnail_txt(
    episode: dict[str, Any],
    lang: str,
    day: int,
) -> str:
    is_ml = lang == "ml"
    title = episode.get("title_ml" if is_ml else "title", episode.get("title", f"Day {day}"))
    hook = episode.get("hook_ml" if is_ml else "hook_en", "")
    scenes = episode.get("scenes", [])
    hook_scene = _first_hook_scene(scenes) if scenes else None
    outro_hook = _last_hook_scene(scenes) if scenes else None

    primary = hook
    if hook_scene:
        overlay = hook_scene.get(f"title_overlay_{lang}", "")
        if overlay:
            primary = overlay
    if not primary:
        primary = title

    secondary = title
    alt_hook = ""
    if outro_hook and outro_hook is not hook_scene:
        alt_hook = outro_hook.get(f"title_overlay_{lang}", "") or ""

    visual_prompt = ""
    if hook_scene and hook_scene.get("prompt"):
        visual_prompt = hook_scene["prompt"]
    elif episode.get("prompts"):
        visual_prompt = episode["prompts"][0]

    lang_label = "MALAYALAM" if is_ml else "ENGLISH"
    lines = [
        f"YOUTUBE THUMBNAIL COPY — {lang_label} (Day {day})",
        "=" * 44,
        "",
        "PRIMARY TEXT (large — 2 to 4 words max on thumbnail):",
        primary.upper() if not is_ml else primary,
        "",
        "SECONDARY TEXT (smaller line under/beside primary):",
        secondary,
        "",
        "EPISODE TITLE (description / upload title reference):",
        title,
        "",
    ]
    if hook and hook != primary:
        lines.extend(["HOOK TAGLINE:", hook, ""])
    if alt_hook:
        lines.extend(["ALT HOOK (outro card option):", alt_hook, ""])
    if visual_prompt:
        lines.extend(
            [
                "VISUAL REFERENCE (use scene 1 / hook frame image):",
                visual_prompt,
                "",
            ]
        )
    upload_title = f"{primary} | {title}" if primary.upper() != title.upper() else title
    lines.extend(
        [
            "WHY THIS MATTERS (VIEWS & REVENUE):",
            "- Thumbnail + title drive click-through rate (CTR) before anyone watches",
            "- Higher CTR → YouTube pushes more impressions → more views → more revenue",
            "- A weak thumbnail can cut views in half even with great video content",
            "- Test primary vs alt hook after 48h; swap thumbnail if CTR stays below ~5%",
            "",
            "SUGGESTED YOUTUBE UPLOAD TITLE:",
            upload_title,
            "",
            "CTR DESIGN NOTES:",
            "- Bold high-contrast text on dark cinematic background",
            "- Face close-up or action moment from hook scene",
            "- Max 3 words on primary text — readability on mobile",
            "- Red/orange accent glow for action; blue/white for ice/Norse scenes",
            "",
            f"Output video: Day_{day}_{'Malayalam' if is_ml else 'English'}.mp4",
        ]
    )
    return "\n".join(lines)


def write_thumbnail_txt_files(episode: dict[str, Any], day: int) -> tuple[Path, Path]:
    """Export separate English and Malayalam YouTube thumbnail copy files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    en_path = OUTPUT_DIR / f"Day_{day}_English_thumbnail.txt"
    ml_path = OUTPUT_DIR / f"Day_{day}_Malayalam_thumbnail.txt"
    en_path.write_text(_build_thumbnail_txt(episode, "en", day), encoding="utf-8")
    ml_path.write_text(_build_thumbnail_txt(episode, "ml", day), encoding="utf-8")
    log.info("Thumbnail copy saved: %s, %s", en_path.name, ml_path.name)
    return en_path, ml_path


def _load_subtitle_font(lang: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if lang == "ml":
        candidates = (
            "Nirmala UI",
            "Nirmala.ttf",
            "Kartika.ttf",
            "NotoSansMalayalam-Regular.ttf",
            "DejaVuSans.ttf",
        )
    else:
        candidates = ("arial.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf")
    for font_name in candidates:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return _load_title_font(size)


def _wrap_subtitle_lines(
    text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int
) -> list[str]:
    overlay = Image.new("RGBA", (max_width, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font, stroke_width=2)
        if (bbox[2] - bbox[0]) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= SUBTITLE_MAX_LINES:
            break
    if current and len(lines) < SUBTITLE_MAX_LINES:
        lines.append(current)
    return lines[:SUBTITLE_MAX_LINES]


def _make_subtitle_overlay(text: str, width: int, height: int, lang: str) -> np.ndarray | None:
    if not text:
        return None
    font_size = SUBTITLE_FONT_SIZE_ML if lang == "ml" else SUBTITLE_FONT_SIZE_EN
    font = _load_subtitle_font(lang, font_size)
    max_width = int(width * 0.92)
    lines = _wrap_subtitle_lines(text, font, max_width)
    if not lines:
        return None

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    line_height = font_size + 10
    block_height = len(lines) * line_height + 24
    y_start = height - SUBTITLE_BOTTOM_PADDING - block_height

    for line_idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=2)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) // 2
        y = y_start + 12 + line_idx * line_height
        pad = 8
        draw.rectangle(
            (x - pad, y - 4, x + text_w + pad, y + line_height - 6),
            fill=(0, 0, 0, 170),
        )
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text(
            (x, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255),
        )
    return np.array(overlay)


def _blend_rgba_overlay(frame: np.ndarray, overlay: np.ndarray) -> np.ndarray:
    alpha = overlay[:, :, 3:4] / 255.0
    rgb = overlay[:, :, :3]
    return (frame * (1.0 - alpha) + rgb * alpha).astype(np.uint8)


def _load_title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_title_overlay(text: str, width: int, height: int) -> np.ndarray | None:
    if not text:
        return None
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_title_font(82)
    stroke = 6
    shadow = 4
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) // 2
    y = (height - text_h) // 2
    draw.text(
        (x + shadow, y + shadow),
        text,
        font=font,
        fill=(0, 0, 0, 170),
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=stroke,
        stroke_fill=(180, 20, 20, 255),
    )
    return np.array(overlay)


def create_cinematic_clip(
    img_path: Path,
    duration: float,
    motion: str = "zoom_in",
    overlay_text: str | None = None,
    subtitle_text: str | None = None,
    subtitle_lang: str = "en",
) -> VideoClip:
    """Ken Burns / pan move with letterboxing, hook titles, and burned-in subtitles."""
    motion = normalize_motion(motion)
    pil_img = Image.open(img_path).convert("RGB")
    iw, ih = pil_img.size
    tw = VIDEO_WIDTH
    content_h = VIDEO_HEIGHT - (2 * LETTERBOX_BAR_HEIGHT)
    th = content_h
    scale_cover = max(tw / iw, th / ih)
    zoom_delta = KEN_BURNS_ZOOM_END - 1.0
    title_overlay = _make_title_overlay(overlay_text, tw, th) if overlay_text else None
    subtitle_overlay = (
        _make_subtitle_overlay(subtitle_text, tw, th, subtitle_lang) if subtitle_text else None
    )

    def crop_rect(progress: float) -> tuple[int, int, int, int]:
        if motion == "zoom_in":
            scale = scale_cover * (1.0 + zoom_delta * progress)
        elif motion == "zoom_out":
            scale = scale_cover * (KEN_BURNS_ZOOM_END - zoom_delta * progress)
        elif motion == "whip_pan":
            scale = scale_cover * (WHIP_PAN_ZOOM - zoom_delta * 0.35 * progress)
        elif motion == "tracking_shot":
            scale = scale_cover * (1.0 + (TRACKING_ZOOM - 1.0) * progress)
        else:
            scale = scale_cover * (KEN_BURNS_ZOOM_END * 0.96)

        nw = max(int(iw * scale), tw)
        nh = max(int(ih * scale), th)

        if motion == "pan_left":
            left = max(int((nw - tw) * (1.0 - progress)), 0)
        elif motion == "pan_right":
            left = max(int((nw - tw) * progress), 0)
        elif motion == "whip_pan":
            whip = math.sin(progress * math.pi * 0.85)
            left = max(int((nw - tw) * (0.15 + 0.75 * whip)), 0)
        elif motion == "tracking_shot":
            left = max(int((nw - tw) * (0.1 + 0.65 * progress)), 0)
        else:
            left = max((nw - tw) // 2, 0)
        top = max((nh - th) // 2, 0)
        return nw, nh, left, top

    def make_frame(t: float) -> np.ndarray:
        linear = min(max(t / max(duration, 0.001), 0.0), 1.0)
        progress = _eased_progress(linear, motion)
        nw, nh, left, top = crop_rect(progress)
        resized = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)
        frame = np.array(resized.crop((left, top, left + tw, top + th)))

        if title_overlay is not None:
            frame = _blend_rgba_overlay(frame, title_overlay)

        if subtitle_overlay is not None:
            frame = _blend_rgba_overlay(frame, subtitle_overlay)

        full = np.zeros((VIDEO_HEIGHT, tw, 3), dtype=np.uint8)
        full[LETTERBOX_BAR_HEIGHT : LETTERBOX_BAR_HEIGHT + th, :] = frame
        return full

    clip = VideoClip(make_frame, duration=duration)
    if hasattr(clip, "with_fps"):
        return clip.with_fps(VIDEO_FPS)
    return clip.set_fps(VIDEO_FPS)


def _ken_burns_clip(img_path: Path, duration: float):
    """Legacy wrapper — simple zoom-in for flat-script episodes."""
    return create_cinematic_clip(img_path, duration, motion="zoom_in")


def _video_is_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 100_000:
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return False
        return float(result.stdout.strip()) > 1.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _safe_unlink(path: Path, retries: int = 8) -> None:
    for attempt in range(retries):
        try:
            if path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.25 * (attempt + 1))


def _cleanup_moviepy_temp(stem: str) -> None:
    for temp in MOVIEPY_TEMP_DIR.glob(f"{stem}*TEMP_MPY*"):
        _safe_unlink(temp)


def build_cinematic_video(
    scene_shot_plans: list[dict[str, Any]],
    scene_audio_paths: list[Path],
    output_path: Path,
    overlay_lang: str,
    bgm_library: dict[str, Path] | None = None,
    bgm_volume: float = DEFAULT_BGM_VOLUME,
) -> Path:
    """Build scene-synced trailer with sub-shots, camera motion, and per-scene BGM cues."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    MOVIEPY_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    local_out = MOVIEPY_TEMP_DIR / output_path.name
    _safe_unlink(local_out)
    _cleanup_moviepy_temp(output_path.stem)

    clips: list[VideoClip] = []
    scene_audios: list[Any] = []
    bgm_tracks: list[Any] = []
    mixed_audios: list[Any] = []
    mixed_voice_ids: set[int] = set()
    nested_clips: list[VideoClip] = []
    video = None
    try:
        for plan, audio_path in zip(scene_shot_plans, scene_audio_paths):
            scene = plan["scene"]
            if not audio_path.exists():
                raise FileNotFoundError(f"Scene audio not found: {audio_path}")

            scene_audio = AudioFileClip(str(audio_path))
            scene_audios.append(scene_audio)
            duration = scene_audio.duration
            overlay = scene.get(f"title_overlay_{overlay_lang}", "") or None
            if scene.get("type") != "hook":
                overlay = scene.get(f"title_overlay_{overlay_lang}") or None
            subtitle = get_scene_subtitle(scene, overlay_lang)

            sub_clips: list[VideoClip] = []
            for shot_idx, shot in enumerate(plan["shots"]):
                shot_overlay = overlay if shot_idx == 0 else None
                sub = create_cinematic_clip(
                    shot["img_path"],
                    shot["duration"],
                    motion=shot["motion"],
                    overlay_text=shot_overlay,
                    subtitle_text=subtitle,
                    subtitle_lang=overlay_lang,
                )
                sub = _clip_set_duration(sub, shot["duration"])
                sub_clips.append(sub)

            if len(sub_clips) == 1:
                scene_video = sub_clips[0]
            else:
                scene_video = concatenate_videoclips(sub_clips, method="compose")
                nested_clips.extend(sub_clips)

            scene_video = _clip_set_duration(scene_video, duration)

            if bgm_library:
                scene_audio_mix, bgm_track = mix_scene_voice_with_cue_bgm(
                    scene_audio,
                    plan["shots"],
                    scene,
                    bgm_library,
                    bgm_volume,
                )
                if bgm_track is not None:
                    bgm_tracks.append(bgm_track)
                    mixed_audios.append(scene_audio_mix)
                    mixed_voice_ids.add(id(scene_audio))
                    scene_video = _clip_set_audio(scene_video, scene_audio_mix)
                else:
                    scene_video = _clip_set_audio(scene_video, scene_audio)
            else:
                scene_video = _clip_set_audio(scene_video, scene_audio)

            clips.append(scene_video)

        video = concatenate_videoclips(clips, method="compose")

        write_kwargs: dict[str, Any] = {
            "fps": VIDEO_FPS,
            "codec": "libx264",
            "audio_codec": "aac",
            "logger": None,
            "temp_audiofile_path": str(MOVIEPY_TEMP_DIR),
            "remove_temp": False,
        }
        if hasattr(video, "write_videofile"):
            try:
                video.write_videofile(str(local_out), **write_kwargs)
            except TypeError:
                write_kwargs.pop("logger", None)
                video.write_videofile(str(local_out), **write_kwargs)
    finally:
        if video is not None:
            video.close()
        for track in mixed_audios:
            track.close()
        for track in bgm_tracks:
            track.close()
        for audio in scene_audios:
            if id(audio) not in mixed_voice_ids:
                audio.close()
        for clip in clips:
            clip.close()
        for clip in nested_clips:
            clip.close()
        _cleanup_moviepy_temp(output_path.stem)

    if not local_out.exists() or local_out.stat().st_size == 0:
        raise RuntimeError(f"Video export failed: {local_out}")

    if output_path.exists():
        _safe_unlink(output_path)
    shutil.move(str(local_out), str(output_path))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Video export failed: {output_path}")

    log.info("Cinematic video exported: %s", output_path)
    return output_path


def _chunk_subtitle_text(text: str, parts: int) -> list[str]:
    words = text.split()
    if not words or parts <= 0:
        return [""] * max(parts, 0)
    if parts == 1:
        return [text]
    chunks: list[str] = []
    words_per = max(1, math.ceil(len(words) / parts))
    for i in range(parts):
        segment = words[i * words_per : (i + 1) * words_per]
        chunks.append(" ".join(segment) if segment else "")
    while len(chunks) < parts:
        chunks.append("")
    return chunks[:parts]


def build_video(
    image_paths: list[Path],
    audio_path: Path,
    output_path: Path,
    subtitle_text: str = "",
    subtitle_lang: str = "en",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    MOVIEPY_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    local_out = MOVIEPY_TEMP_DIR / output_path.name
    _safe_unlink(local_out)
    _cleanup_moviepy_temp(output_path.stem)

    audio = AudioFileClip(str(audio_path))
    try:
        duration_each = audio.duration / len(image_paths)
        subtitle_chunks = _chunk_subtitle_text(subtitle_text, len(image_paths))
        clips = []
        for img_path, chunk in zip(image_paths, subtitle_chunks):
            clip = create_cinematic_clip(
                img_path,
                duration_each,
                motion="zoom_in",
                subtitle_text=chunk or None,
                subtitle_lang=subtitle_lang,
            )
            clips.append(clip)

        video = concatenate_videoclips(clips, method="compose")
        video = _clip_set_audio(video, audio)

        write_kwargs: dict[str, Any] = {
            "fps": VIDEO_FPS,
            "codec": "libx264",
            "audio_codec": "aac",
            "logger": None,
            "temp_audiofile_path": str(MOVIEPY_TEMP_DIR),
            "remove_temp": False,
        }
        if hasattr(video, "write_videofile"):
            try:
                video.write_videofile(str(local_out), **write_kwargs)
            except TypeError:
                write_kwargs.pop("logger", None)
                video.write_videofile(str(local_out), **write_kwargs)

        for clip in clips:
            clip.close()
        video.close()
    finally:
        audio.close()
        _cleanup_moviepy_temp(output_path.stem)

    if not local_out.exists() or local_out.stat().st_size == 0:
        raise RuntimeError(f"Video export failed: {local_out}")

    if output_path.exists():
        _safe_unlink(output_path)
    shutil.move(str(local_out), str(output_path))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Video export failed: {output_path}")

    log.info("Video exported: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def cleanup_temp() -> None:
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR, ignore_errors=True)


def stage_episode_assets(
    day: int,
    en_audio: Path,
    ml_audio: Path,
    en_scene_paths: list[Path] | None = None,
    ml_scene_paths: list[Path] | None = None,
    scene_shot_plans: list[dict[str, Any]] | None = None,
    image_paths: list[Path] | None = None,
) -> tuple[list[Path] | None, Path, Path, list[Path] | None, list[Path] | None]:
    """Copy assets to output staging so OneDrive/temp cleanup cannot break long renders."""
    stage = STAGING_DIR / f"day{day:02d}"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)

    staged_images: list[Path] | None = None
    if scene_shot_plans:
        staged_images = []
        for plan in scene_shot_plans:
            for shot_idx, shot in enumerate(plan["shots"], start=1):
                src = shot["img_path"]
                dest = stage / f"scene{plan['scene']['id']:02d}_shot{shot_idx:02d}{src.suffix}"
                shutil.copy2(src, dest)
                shot["img_path"] = dest
                staged_images.append(dest)
    elif image_paths:
        staged_images = []
        for i, src in enumerate(image_paths, start=1):
            dest = stage / f"img{i:02d}{src.suffix}"
            shutil.copy2(src, dest)
            staged_images.append(dest)

    staged_en = stage / "en.mp3"
    staged_ml = stage / "ml.mp3"
    shutil.copy2(en_audio, staged_en)
    shutil.copy2(ml_audio, staged_ml)

    staged_en_scenes: list[Path] | None = None
    staged_ml_scenes: list[Path] | None = None
    if en_scene_paths and ml_scene_paths:
        staged_en_scenes = []
        staged_ml_scenes = []
        scenes_dir = stage / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        for src in en_scene_paths:
            dest = scenes_dir / src.name
            shutil.copy2(src, dest)
            staged_en_scenes.append(dest)
        for src in ml_scene_paths:
            dest = scenes_dir / src.name
            shutil.copy2(src, dest)
            staged_ml_scenes.append(dest)

    log.info("Staged assets for day %s in %s", day, stage)
    return staged_images, staged_en, staged_ml, staged_en_scenes, staged_ml_scenes


def cleanup_staging(day: int) -> None:
    stage = STAGING_DIR / f"day{day:02d}"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)


async def process_episode(episode: dict[str, Any]) -> tuple[Path, Path]:
    day = episode["day"]
    source = episode.get("source", "unknown")
    title_ml = episode.get("title_ml", episode["title"])
    bgm_library = ensure_bgm_library()
    bgm_path = bgm_library.get("default") or ensure_bgm()
    bgm_volume = episode.get("bgm_volume", DEFAULT_BGM_VOLUME)
    log.info("=== Processing Day %s: %s (%s) ===", day, episode["title"], source)
    log.info("Malayalam title: %s", title_ml)

    write_thumbnail_txt_files(episode, day)

    image_paths = None
    en_out = OUTPUT_DIR / f"Day_{day}_English.mp4"
    ml_out = OUTPUT_DIR / f"Day_{day}_Malayalam.mp4"

    if episode.get("format") == "cinematic_scenes":
        scenes = episode["scenes"]
        log.info(
            "Cinematic mode: %s scenes sorted by id (target ~%ss, sub-shots enabled)",
            len(scenes),
            TARGET_DURATION_SEC,
        )

        en_scene_paths = await generate_scene_audios(scenes, VOICE_EN, "en", day)
        ml_scene_paths = await generate_scene_audios(scenes, VOICE_ML, "ml", day)

        en_narration = TEMP_AUDIO_DIR / f"day{day:02d}_en.mp3"
        ml_narration = TEMP_AUDIO_DIR / f"day{day:02d}_ml.mp3"
        concatenate_audio_files(en_scene_paths, en_narration)
        concatenate_audio_files(ml_scene_paths, ml_narration)

        scene_shot_plans = plan_and_download_scene_shots(day, scenes, en_scene_paths)

        _, staged_en, staged_ml, staged_en_scenes, staged_ml_scenes = stage_episode_assets(
            day,
            en_narration,
            ml_narration,
            en_scene_paths,
            ml_scene_paths,
            scene_shot_plans=scene_shot_plans,
        )
        assert staged_en_scenes and staged_ml_scenes

        write_scene_srt(scene_shot_plans, staged_en_scenes, en_out, "en")
        write_scene_srt(scene_shot_plans, staged_ml_scenes, ml_out, "ml")

        if _video_is_valid(en_out):
            log.info("Skipping English render (already exists): %s", en_out)
        else:
            if en_out.exists():
                log.warning("Replacing invalid English video: %s", en_out)
                _safe_unlink(en_out)
            build_cinematic_video(
                scene_shot_plans,
                staged_en_scenes,
                en_out,
                overlay_lang="en",
                bgm_library=bgm_library,
                bgm_volume=bgm_volume,
            )

        if _video_is_valid(ml_out):
            log.info("Skipping Malayalam render (already exists): %s", ml_out)
        else:
            if ml_out.exists():
                log.warning("Replacing invalid Malayalam video: %s", ml_out)
                _safe_unlink(ml_out)
            build_cinematic_video(
                scene_shot_plans,
                staged_ml_scenes,
                ml_out,
                overlay_lang="ml",
                bgm_library=bgm_library,
                bgm_volume=bgm_volume,
            )
    else:
        image_paths = download_episode_images(episode)
        en_audio = TEMP_AUDIO_DIR / f"day{day:02d}_en.mp3"
        ml_audio = TEMP_AUDIO_DIR / f"day{day:02d}_ml.mp3"
        await generate_audio(
            episode["script_en"], VOICE_EN, en_audio, rate=VOICE_EN_RATE, pitch=VOICE_EN_PITCH
        )
        await generate_audio(
            episode["script_ml"], VOICE_ML, ml_audio, rate=VOICE_ML_RATE, pitch=VOICE_ML_PITCH
        )

        en_mixed = mix_voice_with_bgm(en_audio, bgm_path, bgm_volume)
        ml_mixed = mix_voice_with_bgm(ml_audio, bgm_path, bgm_volume)

        staged_images, staged_en, staged_ml, _, _ = stage_episode_assets(
            day,
            en_mixed,
            ml_mixed,
            image_paths=image_paths,
        )

        if _video_is_valid(en_out):
            log.info("Skipping English render (already exists): %s", en_out)
        else:
            if en_out.exists():
                log.warning("Replacing invalid English video: %s", en_out)
                _safe_unlink(en_out)
            build_video(
                staged_images or [],
                staged_en,
                en_out,
                subtitle_text=episode["script_en"],
                subtitle_lang="en",
            )

        if _video_is_valid(ml_out):
            log.info("Skipping Malayalam render (already exists): %s", ml_out)
        else:
            if ml_out.exists():
                log.warning("Replacing invalid Malayalam video: %s", ml_out)
                _safe_unlink(ml_out)
            build_video(
                staged_images or [],
                staged_ml,
                ml_out,
                subtitle_text=episode["script_ml"],
                subtitle_lang="ml",
            )

    cleanup_staging(day)
    return en_out, ml_out


async def async_main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    day = state["current_day"]

    if day > 30:
        log.info("All 30 days complete. Reset state.json to current_day=1 to restart.")
        return

    episode = get_episode(day)
    try:
        en_path, ml_path = await process_episode(episode)
        state["current_day"] = day + 1
        save_state(state)
        log.info("Success! Day %s complete.", day)
        log.info("  English:   %s", en_path)
        log.info("  Malayalam: %s", ml_path)
        log.info("Next run will process day %s.", state["current_day"])
    except Exception:
        log.exception("Pipeline failed on day %s (current_day NOT incremented).", day)
        raise
    finally:
        cleanup_temp()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
        cleanup_temp()
        sys.exit(130)


if __name__ == "__main__":
    main()
