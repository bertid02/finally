<#
.SYNOPSIS
    Start FinAlly (Windows PowerShell).

.DESCRIPTION
    Idempotent: an existing container is replaced, not duplicated. The database
    lives in the 'finally-data' Docker volume, so replacing the container keeps
    the portfolio. Uses the same image tag, container name and volume as
    docker-compose.yml, so the two ways of running are interchangeable.

.PARAMETER Build
    Force a rebuild of the image before starting.

.PARAMETER NoOpen
    Do not open a browser.

.EXAMPLE
    .\scripts\start_windows.ps1
    .\scripts\start_windows.ps1 -Build
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'

$Image     = 'finally:latest'
$Container = 'finally'
$Volume    = 'finally-data'
$Port      = 8000
$Url       = "http://localhost:$Port"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH. Install Docker Desktop: https://docs.docker.com/desktop/"
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: the Docker daemon is not reachable." -ForegroundColor Red
    Write-Host "Start Docker Desktop and wait for it to report 'running', then re-run this script."
    exit 1
}

if (-not (Test-Path (Join-Path $Root '.env'))) {
    Write-Host "Error: no .env file at $Root\.env" -ForegroundColor Red
    Write-Host ""
    Write-Host "Create one from the template and add your OpenRouter key:"
    Write-Host "    Copy-Item .env.example .env"
    Write-Host "    notepad .env        # set OPENROUTER_API_KEY"
    Write-Host ""
    Write-Host "Leave MASSIVE_API_KEY empty to use the built-in market simulator."
    exit 1
}

docker image inspect $Image *> $null
$imageMissing = ($LASTEXITCODE -ne 0)

if ($Build -or $imageMissing) {
    Write-Host "==> Building $Image (first build takes a few minutes)"
    docker build -t $Image .
    if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed."; exit 1 }
} else {
    Write-Host "==> Using existing image $Image (pass -Build to rebuild)"
}

# Replace any previous container. The volume is untouched, so data survives.
docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "==> Removing previous container '$Container' (the $Volume volume is kept)"
    docker rm -f $Container *> $null
}

Write-Host "==> Starting $Container on port $Port"
docker run -d `
    --name $Container `
    --env-file .env `
    -p "${Port}:8000" `
    -v "${Volume}:/app/db" `
    --restart unless-stopped `
    $Image *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "docker run failed. Is port $Port already in use?"; exit 1 }

# Wait for the app rather than telling the user to refresh until it works.
Write-Host -NoNewline "==> Waiting for $Url/api/health "
$ready = $false
foreach ($i in 1..60) {
    try {
        $resp = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }

    $running = docker ps -q -f "name=^$Container$"
    if (-not $running) {
        Write-Host ""
        Write-Host "Error: the container exited during startup. Logs:" -ForegroundColor Red
        docker logs --tail 40 $Container
        exit 1
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 1
}
if ($ready) { Write-Host " ready" } else { Write-Host " (still starting)" }

Write-Host ""
Write-Host "FinAlly is running at $Url"
Write-Host "  logs:  docker logs -f $Container"
Write-Host "  stop:  .\scripts\stop_windows.ps1"

if (-not $NoOpen) { Start-Process $Url }
