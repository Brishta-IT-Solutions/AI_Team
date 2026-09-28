# Share the Control Center with everyone on your local network (Windows).
#
#   Run from the AI_Team folder:
#     powershell -ExecutionPolicy Bypass -File scripts\share-on-lan.ps1
#
# It makes sure .env has an access code (creating a random one if not), starts everything with
# Docker, then prints the address and code to give your colleagues.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$envPath = Join-Path (Get-Location) ".env"
$utf8 = New-Object System.Text.UTF8Encoding $false

if (-not (Test-Path $envPath)) {
    Copy-Item ".env.example" $envPath
    Write-Host "Created .env from .env.example - add your AI keys there later."
}

$text = [System.IO.File]::ReadAllText($envPath)
$found = [regex]::Match($text, '(?m)^AITC_ACCESS_CODE=(.*)$')
$code = if ($found.Success) { $found.Groups[1].Value.Trim() } else { "" }
if (-not $code) {
    $bytes = New-Object byte[] 6
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $hex = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    $code = "{0}-{1}-{2}" -f $hex.Substring(0, 4), $hex.Substring(4, 4), $hex.Substring(8, 4)
    if ($found.Success) {
        $text = [regex]::Replace($text, '(?m)^AITC_ACCESS_CODE=.*$', "AITC_ACCESS_CODE=$code")
    } else {
        $text = $text.TrimEnd() + "`r`nAITC_ACCESS_CODE=$code`r`n"
    }
    [System.IO.File]::WriteAllText($envPath, $text, $utf8)
    Write-Host "Set a new access code in .env."
}

Write-Host "Starting the Control Center (the first build takes a few minutes)..."
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "docker compose failed - is Docker Desktop running?" }

Write-Host "Waiting for the web app..."
$ready = $false
for ($i = 0; $i -lt 90 -and -not $ready; $i++) {
    try {
        Invoke-WebRequest -Uri "http://localhost:3000/access" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $ready = $true
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw "The web app didn't start. Check: docker compose logs web api" }

# This PC's address on the local network (skips Docker/WSL/VPN adapters and self-assigned addresses).
$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and
                   $_.InterfaceAlias -notmatch "vEthernet|WSL|Docker|Loopback|VirtualBox|VMware|Tailscale|ZeroTier" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
Write-Host "The Control Center is shared on your network." -ForegroundColor Green
foreach ($ip in $ips) { Write-Host ("  Address:     http://{0}:3000" -f $ip) -ForegroundColor Cyan }
Write-Host  "  Access code: $code" -ForegroundColor Cyan
Write-Host ""
Write-Host "If Windows asks whether Docker may accept connections on private networks, click Allow."
Write-Host "The code keeps strangers out; anyone who has it can still choose who they act as."
Write-Host "Stop sharing any time with: docker compose down"
