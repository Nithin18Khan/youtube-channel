#!/usr/bin/env python3
"""Generate scripts/season_XX/day_YY_script.json from scripts/season_definitions.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DEFINITIONS = BASE_DIR / "scripts" / "season_definitions.json"
PROMPT_SUFFIX = (
    ", Unreal Engine 5 render, dynamic embers flying, volumetric smoke, "
    "dramatic cinematic side lighting, depth of field, 8k resolution"
)


def build_scene(beat: dict[str, Any], visual_style: str, scene_id: int) -> dict[str, Any]:
    visual = beat.get("visual", "cinematic action scene")
    style = visual_style.rstrip(", ")
    if "8k resolution" in style.lower():
        prompt = f"{visual}, {style}"
    else:
        prompt = f"{visual}, {style}{PROMPT_SUFFIX}"
    scene: dict[str, Any] = {
        "id": scene_id,
        "type": beat.get("type", "action"),
        "pace": beat.get("pace", beat.get("type", "action")),
        "motion": beat.get("motion", "zoom_in"),
        "voiceover_en": beat["voiceover_en"],
        "voiceover_ml": beat["voiceover_ml"],
        "prompt": prompt,
    }
    if beat.get("type") == "hook" and scene_id in (1, 12):
        hook_en = beat.get("title_overlay_en") or beat["voiceover_en"].split(":", 1)[-1].strip()[:40]
        hook_ml = beat.get("title_overlay_ml") or beat["voiceover_ml"].split(":", 1)[-1].strip()[:40]
        scene["title_overlay_en"] = hook_en.upper() if hook_en else "EPIC!"
        scene["title_overlay_ml"] = hook_ml
    if beat.get("complex_action"):
        scene["complex_action"] = True
    return scene


def episode_to_script(ep: dict[str, Any], season_data: dict[str, Any]) -> dict[str, Any]:
    beats = ep["beats"]
    if len(beats) < 10 or len(beats) > 12:
        raise ValueError(f"Day {ep['day']} needs 10-12 beats, got {len(beats)}")
    visual_style = season_data["visual_style"]
    scenes = [build_scene(b, visual_style, i + 1) for i, b in enumerate(beats)]
    return {
        "day": ep["day"],
        "season": int(season_data.get("season", 0)),
        "title_en": ep["title_en"],
        "title_ml": ep["title_ml"],
        "hook_en": ep["hook_en"],
        "hook_ml": ep["hook_ml"],
        "bgm_volume": 0.22,
        "scenes": scenes,
    }


def write_scripts(season_num: int, definitions: dict[str, Any], *, force: bool = False) -> int:
    key = str(season_num)
    season_data = definitions["seasons"][key]
    season_data["season"] = season_num
    out_dir = BASE_DIR / "scripts" / f"season_{season_num:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for ep in season_data["episodes"]:
        day = int(ep["day"])
        out_path = out_dir / f"day_{day:02d}_script.json"
        if out_path.exists() and not force:
            continue
        script = episode_to_script(ep, season_data)
        out_path.write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate season script JSON files")
    parser.add_argument("--season", type=int, help="Single season 2-12")
    parser.add_argument("--all", action="store_true", help="Generate seasons 2-12")
    parser.add_argument("--force", action="store_true", help="Overwrite existing scripts")
    args = parser.parse_args()

    if not DEFINITIONS.exists():
        print(f"Missing {DEFINITIONS} — run story generator first.", file=sys.stderr)
        sys.exit(1)

    definitions = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    seasons = range(2, 13) if args.all else ([args.season] if args.season else [])
    if not seasons:
        parser.error("Specify --season N or --all")

    total = 0
    for s in seasons:
        if str(s) not in definitions.get("seasons", {}):
            print(f"Season {s} not in definitions, skipping.", file=sys.stderr)
            continue
        count = write_scripts(s, definitions, force=args.force)
        print(f"Season {s:02d}: wrote {count} scripts -> scripts/season_{s:02d}/")
        total += count
    print(f"Done. {total} script files written.")


if __name__ == "__main__":
    main()
