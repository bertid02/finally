<#
.SYNOPSIS
    Stop FinAlly (Windows PowerShell).

.DESCRIPTION
    Stops and removes the container. The 'finally-data' volume is deliberately
    left alone, so the portfolio, watchlist and chat history survive. To discard
    the data as well, run explicitly:

        docker volume rm finally-data

    Idempotent: stopping something already stopped is a no-op, not an error.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Container = 'finally'
$Volume    = 'finally-data'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not on PATH."
    exit 1
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running - nothing to stop."
    exit 0
}

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "==> Stopping and removing '$Container'"
    docker rm -f $Container *> $null
    Write-Host "Stopped. Data kept in the '$Volume' volume."
} else {
    Write-Host "No '$Container' container is running. Data kept in the '$Volume' volume."
}
