#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$KeepConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'LightHouse'
$InstallRoot = if ($env:LIGHTHOUSE_HOME) { [IO.Path]::GetFullPath($env:LIGHTHOUSE_HOME) } else { Join-Path $HOME '.lighthouse' }
$BinDir = Join-Path $InstallRoot 'bin'

Write-Host 'Removing the LightHouse Windows background task' -ForegroundColor Cyan
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $filtered = @($userPath -split ';' | Where-Object {
        $_ -and $_.TrimEnd('\') -ine $BinDir.TrimEnd('\')
    }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $filtered, 'User')
}

if ($KeepConfig) {
    Write-Host 'Removing application files while preserving config and encrypted secrets' -ForegroundColor Cyan
    foreach ($name in @('app', 'venv', 'bin', 'logs', 'start-server.ps1')) {
        Remove-Item -LiteralPath (Join-Path $InstallRoot $name) -Recurse -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host 'Removing LightHouse application files and user-bound encrypted secrets' -ForegroundColor Cyan
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
    $secretRoot = Join-Path $env:LOCALAPPDATA 'LightHouse\secrets'
    Remove-Item -LiteralPath $secretRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'LightHouse was removed. PostgreSQL and its databases were left untouched.' -ForegroundColor Green
