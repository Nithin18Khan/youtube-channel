#!/usr/bin/env python3
"""Audit Malayalam voiceover quality in script JSON files (no API needed)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Latin letters outside speaker tags / known game names suggest bad translation.
LATIN_IN_ML = re.compile(r"[A-Za-z]{3,}")
SPEAKER_TAG = re.compile(r"^\[[^\]]+\]:\s*")
ROMANIZED = re.compile(
    r"\b(enikku|aayi|krodham|prajwalanikkunnu|think you|weapon|path|vaydu|pitaavinte|Thudarnna)\b",
    re.I,
)


def load_scripts(season: int, day_from: int, day_to: int) -> list[tuple[Path, dict]]:
    folder = BASE_DIR / "scripts" / f"season_{season:02d}"
    out: list[tuple[Path, dict]] = []
    for day in range(day_from, day_to + 1):
        path = folder / f"day_{day:02d}_script.json"
        if not path.exists():
            continue
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def ml_body(line: str) -> str:
    return SPEAKER_TAG.sub("", line).strip()


def audit_episode(path: Path, data: dict) -> list[str]:
    issues: list[str] = []
    day = data.get("day", "?")
    seen_in_ep: set[str] = set()
    for scene in data.get("scenes", []):
        sid = scene.get("id", "?")
        vo = scene.get("voiceover_ml", "")
        if not vo:
            issues.append(f"  day {day} scene {sid}: missing voiceover_ml")
            continue
        body = ml_body(vo)
        if body in seen_in_ep:
            issues.append(f"  day {day} scene {sid}: duplicate line in episode")
        seen_in_ep.add(body)
        if ROMANIZED.search(vo):
            issues.append(f"  day {day} scene {sid}: romanized/English mix -> {vo[:70]}...")
        elif LATIN_IN_ML.search(body):
            issues.append(f"  day {day} scene {sid}: Latin in Malayalam -> {vo[:70]}...")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Malayalam script quality")
    parser.add_argument("--season", type=int, default=2)
    parser.add_argument("--from-day", type=int, default=1)
    parser.add_argument("--to-day", type=int, default=30)
    args = parser.parse_args()

    scripts = load_scripts(args.season, args.from_day, args.to_day)
    if not scripts:
        print(f"No scripts in season {args.season:02d}", file=sys.stderr)
        sys.exit(1)

    all_lines: Counter[str] = Counter()
    per_day_issues: list[str] = []
    for path, data in scripts:
        for scene in data.get("scenes", []):
            vo = scene.get("voiceover_ml", "")
            if vo:
                all_lines[ml_body(vo)] += 1
        per_day_issues.extend(audit_episode(path, data))

    repeats = [(line, count) for line, count in all_lines.items() if count > 1]
    repeats.sort(key=lambda x: -x[1])

    print(f"Season {args.season:02d} days {args.from_day}-{args.to_day}")
    print(f"Episodes scanned: {len(scripts)}")
    print(f"Unique Malayalam lines: {len(all_lines)}")
    print(f"Lines repeated across episodes: {len(repeats)}")
    print()
    if repeats:
        print("Top repeated lines (need Gemini rewrite):")
        for line, count in repeats[:12]:
            preview = line[:80] + ("..." if len(line) > 80 else "")
            try:
                print(f"  x{count}: {preview}")
            except UnicodeEncodeError:
                print(f"  x{count}: [Malayalam line, {len(line)} chars]")
        print()
    if per_day_issues:
        print(f"Issues found: {len(per_day_issues)}")
        for item in per_day_issues[:40]:
            try:
                print(item)
            except UnicodeEncodeError:
                print("  [issue with non-ASCII line — see script file]")
        if len(per_day_issues) > 40:
            print(f"  ... and {len(per_day_issues) - 40} more")
    else:
        print("No romanization / in-episode duplicate issues detected.")


if __name__ == "__main__":
    main()
