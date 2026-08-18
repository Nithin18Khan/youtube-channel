# YouTube Auto-Upload Setup (2 Channels, GitHub Actions)

Upload **English** → [English channel](https://studio.youtube.com/channel/UCJnH0aiSQRq2hODcMUwDJOg)  
Upload **Malayalam** → [Malayalam channel](https://studio.youtube.com/channel/UCSvL2qB1WVJZi_7iOW8M3MA)

**Schedule:** Daily **6:30 PM IST** (13:00 UTC) — optimized for India evening gaming views.

---

## Step 1 — Google Cloud (one time)

1. Open [Google Cloud Console](https://console.cloud.google.com)
2. Create project → enable **YouTube Data API v3**
3. **OAuth consent screen** → External → add scope `youtube.upload`
4. **Credentials** → Create **OAuth client ID** → Desktop app
5. Download JSON → save as:
   ```
   credentials/youtube_client.json
   ```

---

## Step 2 — OAuth tokens (one time, local PC)

Same Google account for both channels. Run **twice** — select the correct channel each time:

```powershell
cd "c:\Users\Verbat_001\OneDrive\Desktop\youtube channel"
pip install google-auth-oauthlib google-api-python-client
python scripts/setup_youtube_oauth.py --channel english
python scripts/setup_youtube_oauth.py --channel malayalam
```

Copy each **refresh token** printed at the end.

---

## Step 3 — GitHub Secrets

Repo → **Settings** → **Secrets and variables** → **Actions** → New secret:

| Secret | Value |
|--------|--------|
| `GEMINI_API_KEY` | Your Gemini key |
| `YOUTUBE_CLIENT_ID` | From `youtube_client.json` → `client_id` |
| `YOUTUBE_CLIENT_SECRET` | From `youtube_client.json` → `client_secret` |
| `YOUTUBE_REFRESH_TOKEN_ENGLISH` | Token from `--channel english` |
| `YOUTUBE_REFRESH_TOKEN_MALAYALAM` | Token from `--channel malayalam` |

**Never commit** tokens or `credentials/` to GitHub.

---

## Step 4 — Push & enable Actions

```powershell
git add .
git commit -m "Add YouTube dual-channel GitHub Actions upload"
git push
```

GitHub → **Actions** → **Daily YouTube Pipeline** → **Run workflow** (test manually first).

---

## What runs daily

1. `python main.py` — renders next episode (Day N)
2. `python upload_day.py` — uploads:
   - `Day_N_English.mp4` → English channel
   - `Day_N_Malayalam.mp4` → Malayalam channel
3. Commits `data/state.json` + `data/upload_state.json`

---

## Revenue / views optimization (built in)

- **Upload time:** 6:30 PM IST (cron `0 13 * * *` UTC)
- **Titles:** from `Day_N_*_thumbnail.txt` (CTR hooks)
- **Descriptions:** hashtags, subscribe CTA, episode numbering
- **Tags:** gaming + language-specific SEO
- **Category:** Gaming (20)
- **Captions:** auto-upload `.srt` subtitles
- **Public** immediately (max reach)

---

## Manual upload (local)

```powershell
python main.py
python upload_day.py --day 3
```

---

## Channel IDs (configured)

| Channel | ID |
|---------|-----|
| English | `UCJnH0aiSQRq2hODcMUwDJOg` |
| Malayalam | `UCSvL2qB1WVJZi_7iOW8M3MA` |

Edit `config/youtube.json` to change schedule or privacy.

---

## Notes

- GitHub free tier: ~2000 min/month — each run ~60–120 min. Monitor usage.
- For heavy renders, use a **self-hosted runner** on your PC instead.
- Add thumbnail JPGs in Canva for higher CTR (`.txt` copy is in `output/`).
- YouTube quota: ~6 uploads/day per project — 2/day (EN+ML) is safe.
