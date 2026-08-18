# Game-Engine-Style Video Guide (Image-to-Motion Hybrid)

True text-to-video AI often distorts faces and warps objects. The professional **free** approach — used for Unity, Blender, and God of War–style cinematics — is the **Image-to-Motion Hybrid Method**.

This project automates Steps 2–3 in `main.py` (Ken Burns camera motion + Edge-TTS voiceover + BGM). Use this guide when writing scene prompts in `scripts/day_XX_script.json`.

---

## Prompt Templates

### Close-Up Character (Intense Dialogue / Emotion)

Use for: hook scenes, character lines (`[KRATOS]:`, `[ARES]:`), emotional beats.

```
Cinematic close-up of a weathered, battle-scarred warrior speaking intensely, intricate steel and leather armor, dramatic rim lighting, volumetric fog, Unreal Engine 5 render style, hyper-detailed, 8k resolution, photorealistic game cutscene aesthetic.
```

**Script example** — add your scene-specific action *before* the style suffix:

```json
"prompt": "Kratos angry face shouting at stormy sky, rain and blood on skin, God of War Ragnarok, Cinematic close-up of a weathered battle-scarred warrior speaking intensely, intricate steel and leather armor, dramatic rim lighting, volumetric fog, Unreal Engine 5 render style, hyper-detailed, 8k resolution, photorealistic game cutscene aesthetic."
```

### Action / Wide Scene (Epic Scale)

Use for: establishing shots, boss reveals, mountain peaks, army battles.

```
Wide-angle cinematic shot of a lone warrior standing on a snowy mountain peak facing a colossal shadow monster, dramatic stormy sky, epic composition, God of War style game graphics, volumetric smoke and snow particles, photorealistic 3D render.
```

**Script example:**

```json
"type": "establishing",
"prompt": "Giant fiery silhouette of Ares descending from burning red clouds above battlefield, Wide-angle cinematic shot, epic composition, God of War style game graphics, dramatic stormy sky, volumetric smoke and snow particles, photorealistic 3D render, 8k resolution."
```

---

## Step-by-Step Workflow

### Step 1: Generate High-Res Character Assets

**Automated (this pipeline):** Pollinations.ai generates images from each scene `prompt` field.

**Manual (higher control):** Use Flux.1 or Leonardo.ai with the templates above. Create 3–5 key character close-ups and 2–3 wide backgrounds. Save to `temp/images/dayXX/` and re-run, or replace Pollinations URLs in a custom workflow.

| Tool | Cost | Best for |
|------|------|----------|
| Pollinations.ai | Free | Automated batch (default in `main.py`) |
| Flux.1 / Leonardo.ai | Free tier | Hero character consistency |
| CapCut Desktop | Free | Manual keyframe editing (see Step 2 alt) |

### Step 2: Animate with Cinematic Camera Motion

**Automated (this pipeline):** `create_cinematic_clip()` applies Ken Burns, pan, whip-pan, and tracking moves per scene `motion` field. Sub-shots auto-split long action scenes with varied camera angles.

| JSON `motion` | Effect |
|---------------|--------|
| `zoom_in` | Slow push-in (CapCut scale 100% → 112%) |
| `zoom_out` | Pull back reveal |
| `pan_left` / `pan_right` | Horizontal drift |
| `whip_pan` | Fast snap pan (combat) |
| `tracking_shot` | Smooth follow move |

**Manual (CapCut Desktop):**

1. Drop generated images on the timeline.
2. Click the **Keyframe** diamond at clip start (Scale: 100%).
3. Move forward ~3 seconds; set Scale ~112% and nudge Position left/right.
4. CapCut interpolates a smooth in-game cutscene push/pan.

### Step 3: Layer Cinematic Audio

**Automated (this pipeline):**

- **Voiceover:** Edge-TTS (`en-US-ChristopherNeural` / `ml-IN-SobhanaNeural`)
- **BGM:** Per-scene cues in `assets/bgm/` (`atmospheric`, `combat`, `epic_hook`)
- **Subtitles:** Burned-in + sidecar `.srt` files

**Manual enhancement:**

| Resource | Use |
|----------|-----|
| [Freesound.org](https://freesound.org) | Footstep echoes, bass drops, metal clangs |
| ElevenLabs (free tier) | Alternative voice if you want a different tone |

Drop SFX into CapCut or DaVinci Resolve on top of exported MP4s for extra polish.

---

## Scene Type → Prompt Style Cheat Sheet

| Scene `type` | Prompt style | Typical `motion` |
|--------------|--------------|------------------|
| `hook` | Close-up + bold title overlay | `zoom_in` |
| `action` | Close-up or dynamic mid-action | `whip_pan`, `tracking_shot` |
| `establishing` | Wide epic scale | `tracking_shot`, `pan_right` |
| `dialogue` | Close-up face | `zoom_in` |

Long action scenes (>3.5s) auto-split into sub-shots with alternating close-up / wide / OTS / low-angle variations.

---

## Outputs Per Episode

```
output/Day_N_English.mp4
output/Day_N_Malayalam.mp4
output/Day_N_English.en.srt
output/Day_N_Malayalam.ml.srt
output/Day_N_English_thumbnail.txt   ← CTR copy for upload
output/Day_N_Malayalam_thumbnail.txt
```

See `ML_LOCALIZATION.md` for Malayalam voiceover writing rules.
