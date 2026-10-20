<#
  Alpha - Windows launcher (PowerShell 5.1+ compatible)

  Usage:
    .\start.ps1              # background start (PORT from .env, default 18890)
    .\start.ps1 -Foreground  # run in foreground (Ctrl+C to stop)
    .\start.ps1 -Port 18891 -BindHost 0.0.0.0 -Reload
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$BindHost = "",
    [switch]$Foreground,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

# --- 1. load .env (KEY=VALUE, # comments, optional quotes) ----------
function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content -Path $Path) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $eq = $t.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $t.Substring(0, $eq).Trim()
        $val = $t.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        [Environment]::SetEnvironmentVariable($key, $val, 'Process')
    }
}
Import-DotEnv (Join-Path $RootDir '.env')

if ($Port -le 0) { $Port = if ($env:PORT) { [int]$env:PORT } else { 18890 } }
if (-not $BindHost) { $BindHost = if ($env:HOST) { $env:HOST } else { '127.0.0.1' } }
if (-not $Reload -and $env:RELOAD -eq '1') { $Reload = $true }

# --- 2. locate python interpreter ----------------------------------
$Python = Join-Path $RootDir '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host "ERROR: virtualenv not found at $Python" -ForegroundColor Red
    Write-Host "Create it first: python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-base.txt"
    exit 1
}

$RunDir = Join-Path $RootDir '.run'
$LogDir = Join-Path $RootDir 'logs'
New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null
$LogFile = Join-Path $LogDir "server-$Port.log"
$PidFile = Join-Path $RunDir "funnel-$Port.pid"
if (Test-Path $LogFile) {
    # The previous instance may still hold the file handle for a moment;
    # deleting is best effort so a restart never fails on a locked log.
    try {
        Remove-Item $LogFile -Force -ErrorAction Stop
    } catch {
        try {
            Add-Content -Path $LogFile -Value "=== restart $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" -ErrorAction SilentlyContinue
        } catch { }
    }
}

# --- 3. port check -------------------------------------------------
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $owning = $listener.OwningProcess
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$owning" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd -and $cmd -match 'uvicorn app\.main:app') {
        Write-Host "Already running: PID=$owning, PORT=$Port" -ForegroundColor Green
        exit 0
    }
    Write-Host "ERROR: port $Port is occupied by another process (PID=$owning)" -ForegroundColor Red
    Write-Host "Free it first, or run: .\start.ps1 -Port <other-port>"
    exit 1
}

# --- 4. uvicorn args -----------------------------------------------
$uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--host', $BindHost, '--port', "$Port")
if ($Reload) {
    $uvicornArgs += @('--reload', '--reload-dir', $RootDir,
        '--reload-include', '*.py', '--reload-include', '*.html',
        '--reload-include', '*.css', '--reload-include', '*.js')
}

Write-Host "Starting Alpha ..."
Write-Host "  python   : $Python"
Write-Host "  url      : http://${BindHost}:$Port"
Write-Host "  log file : $LogFile"

if ($Foreground) {
    Write-Host "Foreground mode. Press Ctrl+C to stop." -ForegroundColor Cyan
    & $Python @uvicornArgs
    exit $LASTEXITCODE
}

# --- 5. background start (stdout+stderr into one log) --------------
# A generated .cmd wrapper avoids Start-Process quoting problems with the
# non-ASCII repo path. The batch body stays pure ASCII and relies on %~dp0,
# so cmd.exe never has to encode the Chinese folder name itself.
$BatchFile = Join-Path $RunDir "start-$Port.cmd"
$batch = @(
    '@echo off'
    'cd /d "%~dp0.."'
    "`".venv\Scripts\python.exe`" -u $($uvicornArgs -join ' ') >> `"logs\server-$Port.log`" 2>&1"
) -join "`r`n"
Set-Content -Path $BatchFile -Value $batch -Encoding ASCII
$proc = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/c', $BatchFile `
    -WorkingDirectory $RootDir `
    -WindowStyle Hidden `
    -PassThru

# The app warms up market data on startup, so allow a generous window.
$ready = $false
for ($i = 0; $i -lt 240; $i++) {
    Start-Sleep -Milliseconds 500
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { $ready = $true; break }
    if ($proc.HasExited) { break }
}

if (-not $ready) {
    Write-Host "Start failed. Check log: $LogFile" -ForegroundColor Red
    Get-Content $LogFile -Tail 40 -ErrorAction SilentlyContinue
    exit 1
}

$svcPid = (Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1).OwningProcess
Set-Content -Path $PidFile -Value $svcPid -Encoding ascii
Write-Host "Started OK: PID=$svcPid, PORT=$Port" -ForegroundColor Green
Write-Host "Open http://${BindHost}:$Port in your browser"
