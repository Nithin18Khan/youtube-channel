#!/usr/bin/env python3
"""Generate Season 1 (Ghost of Sparta) scripts — 30-day Greek origin arc."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "scripts" / "season_01"
LEGACY_DIR = BASE_DIR / "scripts"

VISUAL_STYLE = (
    "God of War Ragnarok cinematic game trailer frame, Unreal Engine 5 render, "
    "glowing fiery blades of chaos, dynamic embers flying, dark storm background with lightning strike, "
    "volumetric smoke, dramatic cinematic side lighting, depth of field, 8k resolution"
)

MOTIONS = [
    "zoom_in", "tracking_shot", "pan_left", "whip_pan", "zoom_out",
    "pan_right", "tracking_shot", "zoom_in", "whip_pan", "pan_left",
    "tracking_shot", "zoom_in",
]
BEAT_TYPES = [
    "hook", "establishing", "action", "action", "establishing", "action",
    "action", "establishing", "action", "action", "establishing", "hook",
]
COMPLEX = {2, 3, 5, 6, 8}

# (title_en, title_ml, hook_en, hook_ml)
EPISODES: list[tuple[str, str, str, str]] = [
    ("THE PACT WITH ARES", "ഏറീസുമായുള്ള രക്തക്കരാർ", "BLOOD PACT!", "രക്തക്കരാർ!"),
    ("CHAINS OF THE WAR GOD", "യുദ്ധദേവന്റെ ചങ്ങലകൾ", "WAR GOD'S CHAINS!", "യുദ്ധദേവന്റെ ചങ്ങല!"),
    ("ASHES OF MARATHON", "മാരaton പോരാട്ടത്തിന്റെ ചാരം", "VILLAGES BURN!", "ഗ്രാമങ്ങൾ കത്തുന്നു!"),
    ("LYDIA'S MEMORY", "ലിഡിയയുടെ ഓർമ്മ", "WIFE REMEMBERED!", "ഭാര്യയുടെ ഓർമ്മ!"),
    ("CALLIOPE'S SONG", "കാലിയോപ്പിയുടെ പാട്ട്", "DAUGHTER'S VOICE!", "മകളുടെ ശബ്ദം!"),
    ("ARES HUNGERS", "ഏറീസിന്റെ വിശപ്പ്", "MORE BLOOD!", "കൂടുതൽ രക്തം!"),
    ("SIEGE OF ATHENS", "ഏതൻസിന്റെ ഉപരോധം", "CITY FALLS!", "നഗരം വീഴുന്നു!"),
    ("THE GHOST RISES", "പ്രേതം ഉയരുന്നു", "GHOST BORN!", "പ്രേതം ജനിച്ചു!"),
    ("SPARTA DOUBTS", "സ്പാർട്ടയുടെ സംശയം", "HOME TURNS!", "സ്വദേശം തിരിഞ്ഞു!"),
    ("OLYMPUS WATCHES", "ഓളിമ്പസ് നോക്കുന്നു", "GODS SEE ALL!", "ദേവന്മാർ കാണുന്നു!"),
    ("ZEUS WHISPERS", "സ്യൂസിന്റെ കാതുകേൾപ്പ്", "KING OF GODS!", "ദേവന്മാരുടെ രാജൻ!"),
    ("BLADES NEVER REST", "വാളുകൾ നിർത്തുന്നില്ല", "ENDLESS WAR!", "അനന്ത യുദ്ധം!"),
    ("RIVER OF THE DEAD", "മരിച്ചവരുടെ നദി", "STYX AWAITS!", "സ്റ്റിക്സ് കാത്തിരിക്കുന്നു!"),
    ("TEMPLE OF ATHENA", "അഥീനയുടെ ക്ഷേത്രം", "WISDOM OR WAR!", "ജ്ഞാനമോ യുദ്ധമോ!"),
    ("THE TRUTH CUTS", "സത്യം വെട്ടിമുറിക്കുന്നു", "MIRROR BREAKS!", "കണ്ണാടി പൊടിയുന്നു!"),
    ("OATH BREAKER", "പ്രതിജ്ഞ തകർക്കുന്നവൻ", "REBELLION!", "കലാപം!"),
    ("ARES DESCENDS", "ഏറീസ് ഇIrangunnu", "WAR GOD COMES!", "യുദ്ധദേവൻ വരുന്നു!"),
    ("CHAINS OF FATE", "വിധിയുടെ ചങ്ങലകൾ", "FATE TIGHTENS!", "വിധി കടുപ്പിക്കുന്നു!"),
    ("FAMILY IN FLAMES", "തീയിലെ കുടുംബം", "MEMORY BURNS!", "ഓർമ്മ കത്തുന്നു!"),
    ("PATH TO OLYMPUS", "ഓളിമ്പിലേക്കുള്ള പാത", "CLIMB TO GODS!", "ദേവന്മാരിലേക്ക്!"),
    ("GODS FEAR HIM", "ദേവന്മാർ അവനെ ഭയപ്പെടുന്നു", "THEY TREMBLE!", "അവർ നടുങ്ങുന്നു!"),
    ("ARENA OF BLOOD", "രക്തത്തിന്റെ കളമേട", "FINAL TRIAL!", "അവസാന പരീക്ഷ!"),
    ("BLADES TURN", "വാളുകൾ തിരിയുന്നു", "MASTER OR SLAVE!", "ആചാര്യനോ അടിമയോ!"),
    ("KILL THE WAR GOD", "യുദ്ധദേവനെ കൊല്ലൂ", "ARES MUST DIE!", "ഏറീസ് മരിക്കണം!"),
    ("ARES FALLS", "ഏറീസ് വീഴുന്നു", "GOD SLAIN!", "ദേവൻ കൊല്ലപ്പെട്ടു!"),
    ("OLYMPUS RAGES", "ഓളിമ്പസ് കോപിക്കുന്നു", "HEAVEN SHAKES!", "സ്വർഗ്ഗം കുലുങ്ങുന്നു!"),
    ("ZEUS REVEALED", "സ്യൂസ് വെളിപ്പെടുന്നു", "FATHER'S LIE!", "പിതാവിന്റെ നുണ!"),
    ("CURSE OF THE GHOST", "പ്രേതത്തിന്റെ ശാപം", "NEVER REST!", "ഒരിക്കലും വിശ്രമമില്ല!"),
    ("THE GHOST WALKS ON", "പ്രേതം നടക്കുന്നു", "NO PEACE!", "ശാന്തിയില്ല!"),
    ("GHOST OF SPARTA — FINALE", "സ്പാർട്ടയുടെ പ്രേതം — അവസാനം", "WAR NEVER ENDS!", "യുദ്ധം അവസാനിക്കില്ല!"),
]

LOCATIONS = [
    ("Spartan battlefield", "സ്പാർട്ടൻ പോരക്കളം"),
    ("burning Greek village", "കത്തുന്ന ഗ്രീക്ക ഗ്രാമം"),
    ("Athens siege walls", "ഏതൻസ് മതിൽ"),
    ("Olympus foothills", "ഓളിമ്പസ് നിരയിറങ്ങുക"),
    ("shrine of Ares", "ഏറീസിന്റെ ക്ഷേത്രം"),
    ("River Styx gate", "സ്റ്റിക്സ് നദി"),
    ("Temple of Athena", "അഥീനയുടെ ക്ഷേത്രം"),
]


def _loc(day: int) -> tuple[str, str]:
    return LOCATIONS[day % len(LOCATIONS)]


def build_en_lines(day: int, title: str, hook: str) -> list[str]:
    loc_en, _ = _loc(day)
    nxt = min(day + 1, 30)
    if day == 30:
        cliff = (
            "[NARRATOR]: Greece bleeds... but beyond the sea, frost and fatherhood wait. "
            "Season 2 — Ragnarok — begins tomorrow."
        )
    else:
        cliff = f"[NARRATOR]: The Ghost rises still. Do not miss day {nxt}."
    return [
        f"[NARRATOR]: Day {day} — {hook} — {title}.",
        f"[NARRATOR]: {loc_en} breathes smoke and ash — Kratos will not retreat.",
        "[KRATOS]: Ares! I serve your war... but I am still a man!",
        "[ARES]: Then bleed for me, Ghost! Leave nothing standing!",
        "[NARRATOR]: Lydia and Calliope flicker in memory — soft, cruel, necessary.",
        "[KRATOS]: Their names are not chains. Your war is!",
        "[NARRATOR]: Blades of Chaos spin — thunder wrapped in fire.",
        "[ZEUS]: Olympus watches the Spartan... and smiles in secret.",
        "[KRATOS]: I will break every god who uses my grief!",
        "[ARES]: You cannot kill war itself, Kratos!",
        "[NARRATOR]: Sparta did not raise a saint. It raised a weapon that learned to think.",
        cliff,
    ]


def build_ml_lines(day: int, title_ml: str, hook_ml: str) -> list[str]:
    _, loc_ml = _loc(day)
    nxt = min(day + 1, 30)
    extras = [
        "രക്തം മണ്ണിൽ പതിക്കുന്നു.",
        "പാപത്തിന്റെ നിഴൽ നീങ്ങുന്നു.",
        "യുദ്ധം നിർത്താതെ തുടരുന്നു.",
        "ദേവന്മാർ മുകളിൽ നോക്കുന്നു.",
        "സ്പാർട്ടൻ നേരെ നിൽക്കുന്നു.",
        "തീ ആകാശം പൊള്ളിക്കുന്നു.",
        "മനുഷ്യത്വം ചോദ്യം ചെയ്യപ്പെടുന്നു.",
        "കഥ പുതിയ മുറിവ് തുറക്കുന്നു.",
        "പ്രതിജ്ഞ തകർക്കപ്പെടുന്നു.",
        "പ്രേതം ഉയർന്നുകൊണ്ടിരിക്കുന്നു.",
        "പ്രതീക്ഷ ഇനിയും ജീവിച്ചിരിക്കുന്നു.",
        "നാളെയ്ക്ക് suspense കാത്തിരിക്കുന്നു.",
    ]
    e1, e2 = extras[day % 12], extras[(day + 5) % 12]
    if day == 30:
        cliff = (
            "[നറേറ്റർ]: ഗ്രീസ് രക്തം കുടിക്കുന്നു... കടലിനപ്പുറം മഞ്ഞും പിതൃത്വവും കാത്തിരിക്കുന്നു. "
            "നാളെ Season 2 — രാഗ്നരോക്ക് — തുടങ്ങുന്നു."
        )
    else:
        cliff = f"[നറേറ്റർ]: പ്രേതം ഉയർന്നുകൊണ്ടിരിക്കുന്നു. Day {nxt} കാണാൻ മറക്കരുത്."
    return [
        f"[നറേറ്റർ]: {hook_ml} — {title_ml}. ദിവസം {day} — കഥാപ്രസംഗം തീവ്രമാകുന്നു!",
        f"[നറേറ്റർ]: {loc_ml} പുകമൂടി... {e1} ക്രാറ്റോസ് പിന്മാറാൻ വന്നവനല്ല.",
        "[ക്രാറ്റോസ്]: ഏറീസ്! നിന്റെ യുദ്ധം ഞാൻ ചെയ്യും... പക്ഷേ ഞാൻ ഇനിയും മനുഷ്യനാണ്!",
        "[ഏറീസ്]: അപ്പോൾ എന്റേക്ക് ചോര ഒഴുക്ക്, പ്രേതമേ! ഒന്നും നിൽക്കരുത്!",
        "[നറേറ്റർ]: ലിഡിയയും കാലിയോപ്പിയും ഓർമ്മയിൽ തിളക്കുന്നു — മധുരവും കഠിനവും.",
        "[ക്രാറ്റോസ്]: അവരുടെ പേരുകൾ ചങ്ങലയല്ല. നിന്റെ യുദ്ധമാണ് ചങ്ങല!",
        f"[നറേറ്റർ]: കോപത്തിന്റെ വാളുകൾ തിരിയുന്നു... {e2}",
        "[സ്യൂസ്]: ഓളിമ്പസ് ഈ സ്പാർട്ടനെ നോക്കുന്നു... രഹസ്യമായി പുഞ്ചിരിക്കുന്നു.",
        "[ക്രാറ്റോസ്]: എന്റെ വേദന ഉപയോഗിക്കുന്ന ഓരോ ദേവനെയും ഞാൻ തകർക്കും!",
        "[ഏറീസ്]: യുദ്ധത്തെ നീ കൊല്ലാൻ പറ്റില്ല, ക്രാറ്റോസ്!",
        "[നറേറ്റർ]: സ്പാർട്ട ഒരു പുണ്യവാനെ ഉയർത്തിയില്ല — ചിന്തിക്കാൻ പഠിച്ച ആയുധത്തെ ഉയർത്തി.",
        cliff,
    ]


def build_visuals(day: int) -> list[str]:
    loc_en, _ = _loc(day)
    return [
        f"Kratos heroic low angle on {loc_en} day {day}",
        f"wide establishing shot {loc_en} storm sky",
        f"Kratos mid battle Blades of Chaos {loc_en}",
        f"Ares fiery vision above battlefield day {day}",
        f"memory ghost Lydia Calliope overlay Kratos",
        f"Kratos shattering chains shrine floor",
        f"slow motion Blades of Chaos strike enemies",
        f"Zeus silhouette Olympus clouds lightning",
        f"Kratos rage close up rain blood",
        f"Ares wounded retreating fire",
        f"wide Sparta red dawn after battle",
        f"Kratos walking away burning ruins epic poster day {day}",
    ]


def build_episode(day: int) -> dict[str, Any]:
    title_en, title_ml, hook_en, hook_ml = EPISODES[day - 1]
    en_lines = build_en_lines(day, title_en, hook_en)
    ml_lines = build_ml_lines(day, title_ml, hook_ml)
    visuals = build_visuals(day)
    beats = []
    for i in range(12):
        beat: dict[str, Any] = {
            "voiceover_en": en_lines[i],
            "voiceover_ml": ml_lines[i],
            "type": BEAT_TYPES[i],
            "motion": MOTIONS[i],
            "visual": visuals[i],
        }
        if i in COMPLEX:
            beat["complex_action"] = True
        if i == 0:
            beat["title_overlay_en"] = hook_en
            beat["title_overlay_ml"] = hook_ml
        if i == 11:
            beat["title_overlay_en"] = "SEASON FINALE" if day == 30 else "TO BE CONTINUED"
            beat["title_overlay_ml"] = "സീസൺ അവസാനം" if day == 30 else "തുടരുന്നു"
        beats.append(beat)
    return {
        "day": day,
        "title_en": title_en,
        "title_ml": title_ml,
        "hook_en": hook_en,
        "hook_ml": hook_ml,
        "beats": beats,
    }


def to_script(ep: dict[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from generate_year_scripts import build_scene  # noqa: WPS433

    scenes = [build_scene(b, VISUAL_STYLE, i + 1) for i, b in enumerate(ep["beats"])]
    return {
        "day": ep["day"],
        "season": 1,
        "title_en": ep["title_en"],
        "title_ml": ep["title_ml"],
        "hook_en": ep["hook_en"],
        "hook_ml": ep["hook_ml"],
        "bgm_volume": 0.22,
        "scenes": scenes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Season 1 Ghost of Sparta scripts")
    parser.add_argument("--from-day", type=int, default=3)
    parser.add_argument("--to-day", type=int, default=30)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--legacy-copy", action="store_true", help="Copy to scripts/day_XX_script.json")
    parser.add_argument("--skip-existing", action="store_true", help="Keep days 1-2 untouched")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for day in range(args.from_day, args.to_day + 1):
        if args.skip_existing and day <= 2 and not args.force:
            continue
        out_path = OUT_DIR / f"day_{day:02d}_script.json"
        ep = build_episode(day)
        script = to_script(ep)
        out_path.write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.legacy_copy:
            shutil.copy2(out_path, LEGACY_DIR / f"day_{day:02d}_script.json")
        written += 1
        print(f"Wrote {out_path.relative_to(BASE_DIR)}")

    print(f"Done. {written} Season 1 scripts (days {args.from_day}-{args.to_day}).")


if __name__ == "__main__":
    main()
