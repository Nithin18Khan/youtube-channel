#!/usr/bin/env python3
"""Refine Malayalam voiceovers via Gemini — South Kerala slang, no repeats, natural TTS."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
PROGRESS_FILE = BASE_DIR / "data" / "gemini_refine_progress.json"
DEFAULT_MODEL = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
HOOK_ML_MAX_LEN = 48

MALAYALAM_SYSTEM = """You are a native South Kerala (Travancore–Kollam–Thiruvananthapuram) Malayalam script doctor for a cinematic gaming YouTube serial (kathaprasangam tone).

AUDIENCE: Kerala + Gulf Malayalis, ages 18–34. Lines are read aloud by TTS — must sound natural when spoken.

STRICT RULES:
1. Write ONLY in Malayalam script (Unicode). Zero English words, zero Manglish, zero romanized Malayalam.
2. South Kerala spoken flavour — warm, dramatic, slightly colloquial but still epic (like temple kathaprasangam + young gamer energy). Examples of acceptable flavour: "അച്ഛാ", "എടാ", "ശരി", "ഇനി നോക്ക്", "കഥ ഇങ്ങനെയാ" — use naturally, not every line.
3. NOT word-for-word translation of English. Match emotion and story beat, rewrite for Malayalam ears.
4. Keep speaker tags exactly: [നറേറ്റർ], [ക്രാറ്റോസ്], [അത്രേയസ്], [ഓഡിൻ], [ഫ്രെയ], etc. — same tag as input.
5. Max 2 short sentences per scene (TTS + subtitles). Punchy, cinematic. Use commas and ellipses (...) for natural pauses — Gemini voice reads pitch from punctuation and scene mood.
6. NEVER repeat a sentence you already used in this episode or in the "already used" list below.
7. Character names (spell correctly): ക്രാറ്റോസ്, അത്രേയസ്, ഏറീസ്, ഓഡിൻ, ഫ്രെയ, സ്യൂസ്, കർണ്ണൻ, റാവണൻ, etc.
8. Do NOT change scene ids, English fields, prompts, motion, type, or complex_action flags.
9. hook_ml: 2-6 words ONLY — short thumbnail punch line, no speaker tags, no long sentences.
10. Return ONLY valid JSON — no markdown fences, no explanation."""

REFINE_USER_TEMPLATE = """Season {season} Day {day}: {title_en} / {title_ml}

English reference (match story beats, rewrite Malayalam naturally):
{english_block}

Current Malayalam (fix all issues — slang, naturalness, no repeats):
{malayalam_block}

Phrases already used in earlier episodes this season (DO NOT reuse verbatim):
{used_phrases}

Output JSON with ONLY these keys:
{{
  "hook_ml": "...",
  "title_ml": "...",
  "scenes": [
    {{"id": 1, "voiceover_ml": "[നറേറ്റർ]: ...", "title_overlay_ml": "..."}},
    ...
  ]
}}

Include title_overlay_ml only for hook scenes (usually scene 1 and last). Every scene needs voiceover_ml."""


def load_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_progress() -> dict[str, Any]:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"done": [], "failed": []}


def save_progress(progress: dict[str, Any]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def episode_key(season: int, day: int) -> str:
    return f"s{season:02d}d{day:02d}"


def collect_used_phrases(season: int, before_day: int, max_phrases: int = 80) -> list[str]:
    folder = BASE_DIR / "scripts" / f"season_{season:02d}"
    phrases: list[str] = []
    for day in range(1, before_day):
        path = folder / f"day_{day:02d}_script.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for scene in data.get("scenes", []):
            vo = scene.get("voiceover_ml", "")
            if vo:
                body = re.sub(r"^\[[^\]]+\]:\s*", "", vo).strip()
                if body and body not in phrases:
                    phrases.append(body)
    return phrases[-max_phrases:]


def build_prompt(script: dict[str, Any], used_phrases: list[str]) -> str:
    en_lines = []
    ml_lines = []
    for scene in script.get("scenes", []):
        sid = scene["id"]
        en_lines.append(f"Scene {sid}: {scene.get('voiceover_en', '')}")
        ml_lines.append(f"Scene {sid}: {scene.get('voiceover_ml', '')}")
    return REFINE_USER_TEMPLATE.format(
        season=script.get("season", 1),
        day=script["day"],
        title_en=script.get("title_en", ""),
        title_ml=script.get("title_ml", ""),
        english_block="\n".join(en_lines),
        malayalam_block="\n".join(ml_lines),
        used_phrases="\n".join(f"- {p}" for p in used_phrases) or "(none yet)",
    )


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def sanitize_hook_ml(hook: str, fallback: str) -> str:
    hook = re.sub(r"^\[[^\]]+\]:\s*", "", hook.strip())
    hook = hook.replace("\n", " ").strip()
    if not hook or len(hook) > HOOK_ML_MAX_LEN or re.search(r"[A-Za-z]{3,}", hook):
        return fallback[:HOOK_ML_MAX_LEN]
    return hook


def merge_refinement(script: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(script)
    fallback_hook = script.get("hook_ml", "") or script.get("title_ml", "!")
    if patch.get("hook_ml"):
        out["hook_ml"] = sanitize_hook_ml(patch["hook_ml"], fallback_hook)
    if patch.get("title_ml"):
        out["title_ml"] = patch["title_ml"]
    patch_scenes = {s["id"]: s for s in patch.get("scenes", [])}
    new_scenes = []
    for scene in out.get("scenes", []):
        s = dict(scene)
        p = patch_scenes.get(s["id"])
        if p:
            if p.get("voiceover_ml"):
                s["voiceover_ml"] = p["voiceover_ml"]
            if p.get("title_overlay_ml"):
                s["title_overlay_ml"] = p["title_overlay_ml"]
        new_scenes.append(s)
    out["scenes"] = new_scenes
    for scene in out.get("scenes", []):
        if scene.get("id") == 1 and scene.get("type") == "hook":
            scene["title_overlay_ml"] = out["hook_ml"]
    return out


class DailyQuotaExceeded(Exception):
    """Gemini free-tier daily request limit hit."""


def call_gemini(system: str, user: str, model: str) -> str:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("Set GEMINI_API_KEY in .env")
    client = genai.Client()
    try:
        response = client.models.generate_content(
            model=model,
            contents=user,
            config={"system_instruction": system},
        )
    except Exception as exc:
        msg = str(exc)
        if "429" in msg and ("PerDay" in msg or "free_tier" in msg.lower() or "FreeTier" in msg):
            raise DailyQuotaExceeded(msg) from exc
        raise
    return response.text or ""


def sync_legacy(script_path: Path) -> None:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    if int(data.get("season", 0)) != 1:
        return
    day = int(data["day"])
    legacy = BASE_DIR / "scripts" / f"day_{day:02d}_script.json"
    legacy.write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")


def refine_script(path: Path, *, model: str, dry_run: bool) -> bool:
    script = json.loads(path.read_text(encoding="utf-8"))
    season = int(script.get("season", 1))
    day = int(script["day"])
    used = collect_used_phrases(season, day)
    user_prompt = build_prompt(script, used)

    if dry_run:
        print(f"[dry-run] Would refine {path.name} ({len(used)} prior phrases blocked)")
        return True

    for attempt in range(3):
        try:
            raw = call_gemini(MALAYALAM_SYSTEM, user_prompt, model)
            patch = parse_json_response(raw)
            merged = merge_refinement(script, patch)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            sync_legacy(path)
            print(f"Refined {path.relative_to(BASE_DIR)}")
            return True
        except DailyQuotaExceeded as exc:
            print(f"  Daily Gemini quota reached: {exc}", file=sys.stderr)
            raise
        except Exception as exc:
            wait = 10 * (attempt + 1)
            print(f"  S{season:02d}D{day:02d} attempt {attempt + 1} failed: {exc}", file=sys.stderr)
            if attempt < 2:
                time.sleep(wait)
    return False


def iter_episodes(
    seasons: list[int], from_day: int, to_day: int
) -> list[tuple[int, int, Path]]:
    jobs: list[tuple[int, int, Path]] = []
    for season in seasons:
        folder = BASE_DIR / "scripts" / f"season_{season:02d}"
        for day in range(from_day, to_day + 1):
            path = folder / f"day_{day:02d}_script.json"
            if path.exists():
                jobs.append((season, day, path))
    return jobs


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Refine Malayalam scripts with Gemini")
    parser.add_argument("--season", type=int, help="Single season 1-12")
    parser.add_argument("--all", action="store_true", help="All 12 seasons × 30 days (360 episodes)")
    parser.add_argument("--from-day", type=int, default=1)
    parser.add_argument("--to-day", type=int, default=30)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-refine even if marked done")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between API calls")
    parser.add_argument(
        "--max-per-run",
        type=int,
        default=18,
        help="Stop after N refinements (free tier ~20/day). Use 0 for no limit.",
    )
    args = parser.parse_args()

    env_max = os.environ.get("GEMINI_REFINE_MAX_PER_RUN")
    if env_max is not None:
        args.max_per_run = int(env_max)

    if args.all:
        seasons = list(range(1, 13))
    elif args.season:
        seasons = [args.season]
    else:
        parser.error("Specify --season N or --all")

    progress = load_progress()
    done_set = set(progress.get("done", []))
    jobs = iter_episodes(seasons, args.from_day, args.to_day)

    ok = 0
    skipped = 0
    failed = 0
    total = len(jobs)

    for idx, (season, day, path) in enumerate(jobs, start=1):
        key = episode_key(season, day)
        if not args.force and key in done_set:
            skipped += 1
            continue
        if args.max_per_run and ok >= args.max_per_run:
            print(f"Reached --max-per-run {args.max_per_run}. Resume tomorrow with same command.")
            break
        print(f"[{idx}/{total}] Season {season:02d} Day {day:02d} ...")
        try:
            if refine_script(path, model=args.model, dry_run=args.dry_run):
                ok += 1
                if not args.dry_run:
                    done_set.add(key)
                    progress["done"] = sorted(done_set)
                    failed_list = [x for x in progress.get("failed", []) if x != key]
                    progress["failed"] = failed_list
                    save_progress(progress)
            else:
                failed += 1
                if not args.dry_run:
                    if key not in progress.get("failed", []):
                        progress.setdefault("failed", []).append(key)
                    save_progress(progress)
        except DailyQuotaExceeded:
            print("Stopping — Gemini daily quota exhausted. Run again tomorrow (IST midnight reset).")
            break
        if not args.dry_run and idx < total:
            time.sleep(args.delay)

    print(
        f"Done. refined={ok} skipped={skipped} failed={failed} total_jobs={total} "
        f"progress={PROGRESS_FILE.relative_to(BASE_DIR)}"
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
