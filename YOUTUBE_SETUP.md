# YouTube Auto-Upload Setup (2 Channels, GitHub Actions)

Upload **English** → [English channel](https://studio.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg)  
Upload **Malayalam** → [Malayalam channel](https://studio.youtube.com/channel/UCSvL2qB1WVJZi_7iOW8M3MA)

**Schedule:** Daily **6:30 PM IST** (13:00 UTC)

---

## How Google Cloud + GitHub Actions work together

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  Google Cloud       │     │  GitHub Actions      │     │  YouTube            │
│  (way finder)       │     │  (daily cron)        │     │  2 channels         │
├─────────────────────┤     ├──────────────────────┤     ├─────────────────────┤
│ OAuth Desktop app   │────▶│ Secrets store tokens │────▶│ English: EN video   │
│ YouTube Data API v3 │     │ Runs main.py         │     │ Malayalam: ML video │
│ youtube.upload scope│     │ Runs upload_day.py   │     │ + thumbnail JPG     │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
         ▲                            │
         │                            │
    One-time sign-in              Every day 6:30 PM IST
    (your PC browser)             Ubuntu cloud runner
```

### Google Cloud (one-time setup — already done)

| Piece | What it does |
|-------|----------------|
| **Project** `way finder` | Holds your YouTube API access |
| **YouTube Data API v3** | Allows upload via code |
| **OAuth Desktop client** | `credentials/youtube_client.json` |
| **Refresh tokens** | Saved in GitHub Secrets — no daily browser login |

Google Cloud does **not** render videos. It only **authorizes** uploads.

### GitHub Actions (runs every day automatically)

File: `.github/workflows/daily-youtube.yml`

| Step | What happens |
|------|----------------|
| 1 | GitHub starts Ubuntu VM at **13:00 UTC (6:30 PM IST)** |
| 2 | `python main.py` → renders **next episode** (reads `data/state.json`) |
| 3 | Creates EN + ML videos, subtitles, **thumbnail JPGs** |
| 4 | `python upload_day.py` → uploads to correct channel |
| 5 | Saves `data/state.json` + `data/upload_state.json` back to repo |

**Tomorrow:** `current_day: 2` → **Day 2** English + Malayalam auto-render + upload.

### Channel routing (fixed)

| Video | Channel |
|-------|---------|
| `Day_N_English.mp4` | English channel only |
| `Day_N_Malayalam.mp4` | Malayalam channel only |

---

## What uploads automatically

- Video (MP4)
- Title, description, tags (from thumbnail.txt)
- **Thumbnail JPG** (generated from hook frame + CTR text)
- SRT captions (when OAuth scope allows)

---

## BGM (background music)

| Cue | Used for |
|-----|----------|
| `epic_hook` | Hook / intro scenes |
| `combat` | Action + establishing scenes |
| `emotional` | Optional per-scene override |
| ~~`atmospheric`~~ | **Disabled** — remapped to `combat` |

---

## Manual commands

```powershell
# Render next episode locally
python main.py

# Upload both channels
python upload_day.py --day 1

# Upload thumbnails only (videos already live)
python upload_day.py --day 1 --thumbnails-only
```

---

## GitHub Secrets (required)

| Secret | Purpose |
|--------|---------|
| `GEMINI_API_KEY` | Malayalam Gemini voice + script refine |
| `GEMINI_REFINE_MAX_PER_RUN` | Optional. Episodes per refine run (`18` free, `100` paid API) |
| `YOUTUBE_CLIENT_ID` | OAuth |
| `YOUTUBE_CLIENT_SECRET` | OAuth |
| `YOUTUBE_REFRESH_TOKEN_ENGLISH` | English channel |
| `YOUTUBE_REFRESH_TOKEN_MALAYALAM` | Malayalam channel |

---

## Notes

- GitHub free tier: ~2000 min/month — each render ~60–120 min. Monitor usage.
- **Malayalam script refine** runs automatically after each daily upload (`refine-malayalam-scripts.yml`). Progress: `data/gemini_refine_progress.json`.
- Free Gemini API: ~18 script refinements/day. Enable billing + set `GEMINI_REFINE_MAX_PER_RUN=100` to finish all 360 scripts in one night.
- Videos are **not** stored in git — cloud runner generates fresh each day.
- For heavy renders, use a **self-hosted runner** on your PC.
