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

$ClientPath = Join-Path $Root "credentials\youtube_client.json"
if (-not (Test-Path $ClientPath)) { throw "Missing $ClientPath" }
$Client = Get-Content $ClientPath -Raw | ConvertFrom-Json
$Block = if ($Client.installed) { $Client.installed } elseif ($Client.web) { $Client.web } else { $Client }
Set-GhSecret "YOUTUBE_CLIENT_ID" $Block.client_id
Set-GhSecret "YOUTUBE_CLIENT_SECRET" $Block.client_secret

foreach ($ch in @("english", "malayalam")) {
    $TokenPath = Join-Path $Root "credentials\token_$ch.json"
    if (-not (Test-Path $TokenPath)) { throw "Missing $TokenPath — run setup_youtube_oauth.py --channel $ch" }
    $Token = (Get-Content $TokenPath -Raw | ConvertFrom-Json).refresh_token
    $SecretName = if ($ch -eq "english") { "YOUTUBE_REFRESH_TOKEN_ENGLISH" } else { "YOUTUBE_REFRESH_TOKEN_MALAYALAM" }
    Set-GhSecret $SecretName $Token
}

$EnvPath = Join-Path $Root ".env"
if (Test-Path $EnvPath) {
    Get-Content $EnvPath | ForEach-Object {
        if ($_ -match '^GEMINI_API_KEY=(.+)$') {
            Set-GhSecret "GEMINI_API_KEY" $Matches[1]
        }
    }
}

Write-Host "`nDone. Verify: gh secret list --repo $Repo"
