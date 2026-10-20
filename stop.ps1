<#
  Alpha - Windows stop script

  Order (per AGENTS.md, avoids stuck SQLite WAL):
    1. WAL checkpoint on every sqlite database
    2. stop the service process
#>
[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Continue'
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

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

$Python = Join-Path $RootDir '.venv\Scripts\python.exe'
$RunDir = Join-Path $RootDir '.run'
$PidFile = Join-Path $RunDir "funnel-$Port.pid"

# --- step 1: sqlite WAL checkpoint ---------------------------------
if (Test-Path $Python) {
    $code = @'
import sqlite3, pathlib
root = pathlib.Path(".")
for db in ["data/funnel_state.db", "data/market_kline.db"]:
    p = root / db
    if not p.exists():
        continue
    try:
        conn = sqlite3.connect(str(p), timeout=5)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        print(f"checkpoint OK: {db}")
    except Exception as e:
        print(f"checkpoint skip: {db} ({e})")
'@
    $code | & $Python - 2>&1 | ForEach-Object { Write-Host $_ }
}

# --- step 2: stop the service --------------------------------------
$targets = @()
if (Test-Path $PidFile) {
    $targets += (Get-Content $PidFile -Raw).Trim()
}
$conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { $targets += [string]$conn.OwningProcess }
$targets = $targets | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique

if (-not $targets -or $targets.Count -eq 0) {
    Write-Host "Service is not running (PORT=$Port)"
} else {
    foreach ($t in $targets) {
        Stop-Process -Id $t -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped PID=$t"
    }
    Start-Sleep -Milliseconds 800
}

Remove-Item $PidFile -ErrorAction SilentlyContinue
Write-Host "Stop done (PORT=$Port)"
