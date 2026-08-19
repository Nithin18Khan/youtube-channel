# Run locally after daily upload (or schedule with Windows Task Scheduler)
# Free tier: ~18 episodes/day | Paid Gemini API: set $env:GEMINI_REFINE_MAX_PER_RUN=100

Set-Location $PSScriptRoot\..

if (-not $env:GEMINI_REFINE_MAX_PER_RUN) {
    $env:GEMINI_REFINE_MAX_PER_RUN = "18"
}

Write-Host "Gemini Malayalam refine (max $env:GEMINI_REFINE_MAX_PER_RUN per run)..."
python scripts/refine_malayalam_gemini.py --all --from-day 1 --to-day 30 --delay 3

python -c @"
import json
from pathlib import Path
p = Path('data/gemini_refine_progress.json')
if p.exists():
    d = json.loads(p.read_text(encoding='utf-8'))
    print('Progress:', len(d.get('done', [])), '/ 360 done | failed:', len(d.get('failed', [])))
"@
