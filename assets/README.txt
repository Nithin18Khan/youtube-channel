BGM library (mood-matched, continuous playback)
=================================================
Place custom tracks in assets/bgm/:

  combat.mp3       - percussion/brass for action scenes (plays through continuously)
  emotional.mp3    - soft piano/strings for resolution / father-son moments
  epic_hook.mp3    - choral/epic hits for hooks, establishing shots, tension build
  default.mp3      - fallback track

JSON scene field: bgm_cue = combat | emotional | epic_hook
(Legacy bgm_cue=atmospheric is remapped to epic_hook automatically.)

Malayalam voice (Gemini TTS - recommended):
  set GEMINI_API_KEY=your_key
  pip install google-genai
