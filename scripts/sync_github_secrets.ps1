# Push YouTube OAuth values to GitHub Actions secrets (run after setup_youtube_oauth.py).
# Requires: gh CLI logged in, credentials/youtube_client.json, token_*.json

$ErrorActionPreference = "Stop"
$Repo = "Nithin18Khan/youtube-channel"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Set-GhSecret($Name, $Value) {
    if (-not $Value) { throw "Empty value for $Name" }
    $Value | gh secret set $Name --repo $Repo
    Write-Host "Set secret: $Name"
}

$ClientCandidates = @(
    (Join-Path $Root "credentials\youtube_client_web.json"),
    (Join-Path $Root "credentials\youtube_client_v2.json"),
    (Join-Path $Root "credentials\youtube_client.json")
)
$ClientPath = $ClientCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ClientPath) { throw "Missing OAuth client JSON in credentials/" }
$Client = Get-Content $ClientPath -Raw | ConvertFrom-Json
$Block = if ($Client.installed) { $Client.installed } elseif ($Client.web) { $Client.web } else { $Client }
Set-GhSecret "YOUTUBE_CLIENT_ID" $Block.client_id
Set-GhSecret "YOUTUBE_CLIENT_SECRET" $Block.client_secret

$MalayalamPath = Join-Path $Root "credentials\token_malayalam.json"
if (-not (Test-Path $MalayalamPath)) { throw "Missing $MalayalamPath - run OAuth Playground + import_youtube_refresh_token.py" }
$MalayalamToken = (Get-Content $MalayalamPath -Raw | ConvertFrom-Json).refresh_token
Set-GhSecret "YOUTUBE_REFRESH_TOKEN_MALAYALAM" $MalayalamToken

$EnglishPath = Join-Path $Root "credentials\token_english.json"
if (Test-Path $EnglishPath) {
    $EnglishToken = (Get-Content $EnglishPath -Raw | ConvertFrom-Json).refresh_token
} else {
    Write-Host "No token_english.json - using Malayalam token for both (single-channel mode)"
    $EnglishToken = $MalayalamToken
}
Set-GhSecret "YOUTUBE_REFRESH_TOKEN_ENGLISH" $EnglishToken

$EnvPath = Join-Path $Root ".env"
if (Test-Path $EnvPath) {
    Get-Content $EnvPath | ForEach-Object {
        if ($_ -match '^GEMINI_API_KEY=(.+)$') {
            Set-GhSecret "GEMINI_API_KEY" $Matches[1]
        }
    }
}

Write-Host "`nDone. Verify: gh secret list --repo $Repo"
