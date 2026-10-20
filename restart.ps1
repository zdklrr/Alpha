<#
  Alpha - Windows restart script
  Order: sqlite checkpoint -> stop -> start
#>
[CmdletBinding()]
param(
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

& (Join-Path $RootDir 'stop.ps1') -Port $Port
Start-Sleep -Seconds 1
& (Join-Path $RootDir 'start.ps1') -Port $Port
