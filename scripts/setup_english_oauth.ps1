# One-click: auto-open English channel in Chrome + OAuth
Set-Location (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
python scripts/setup_youtube_oauth.py --channel english --wait 20
