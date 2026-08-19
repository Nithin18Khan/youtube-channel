# Verify local OAuth files exist before syncing to GitHub Actions.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "=== CI readiness check ===" -ForegroundColor Cyan

$ClientOk = @(
    "$Root\credentials\youtube_client_web.json",
    "$Root\credentials\youtube_client_v2.json",
    "$Root\credentials\youtube_client.json"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $ClientOk) {
    Write-Host "FAIL: No OAuth client JSON in credentials/" -ForegroundColor Red
    exit 1
}
Write-Host "OK: OAuth client -> $ClientOk"

$MalPath = "$Root\credentials\token_malayalam.json"
if (-not (Test-Path $MalPath)) {
    Write-Host "FAIL: Missing credentials\token_malayalam.json" -ForegroundColor Red
    Write-Host "  Run OAuth Playground + scripts/import_youtube_refresh_token.py"
    exit 1
}
Write-Host "OK: Malayalam refresh token file exists"

$State = Get-Content "$Root\data\state.json" -Raw | ConvertFrom-Json
Write-Host "Pipeline state: Day $($State.current_day) (next render)"

Write-Host ""
Write-Host "GitHub Secrets required:" -ForegroundColor Yellow
Write-Host "  https://github.com/Nithin18Khan/youtube-channel/settings/secrets/actions"
Write-Host "  GEMINI_API_KEY"
Write-Host "  YOUTUBE_CLIENT_ID"
Write-Host "  YOUTUBE_CLIENT_SECRET"
Write-Host "  YOUTUBE_REFRESH_TOKEN_MALAYALAM"
Write-Host "  YOUTUBE_REFRESH_TOKEN_ENGLISH (optional, uses Malayalam token in single-channel mode)"
Write-Host ""
Write-Host "Auto-sync (if gh CLI installed):" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/sync_github_secrets.ps1"
Write-Host ""
Write-Host "Then re-run workflow:" -ForegroundColor Green
Write-Host "  https://github.com/Nithin18Khan/youtube-channel/actions/workflows/daily-youtube.yml"
