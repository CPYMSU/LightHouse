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
$ConfigFile = Join-Path $InstallRoot 'config.json'
$VenvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    Write-Host 'Stopping additional managed LightHouse instances' -ForegroundColor Cyan
    $previousHome = $env:LIGHTHOUSE_HOME
    try {
        $env:LIGHTHOUSE_HOME = $InstallRoot
        & $VenvPython -c "from lighthouse.instances import list_instances, stop_instance; [stop_instance(item.id, force=True) for item in list_instances() if item.id != 'default']" 2>$null
    }
    catch {
        Write-Warning "Could not stop every additional instance cleanly: $($_.Exception.Message)"
    }
    finally {
        if ($null -eq $previousHome) {
            Remove-Item Env:LIGHTHOUSE_HOME -ErrorAction SilentlyContinue
        }
        else {
            $env:LIGHTHOUSE_HOME = $previousHome
        }
    }
}

Write-Host 'Removing the LightHouse Windows background task' -ForegroundColor Cyan
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch { }

# Stop only the private PostgreSQL cluster owned by LightHouse. External or
# system-wide PostgreSQL services are never modified by the uninstaller.
if (Test-Path -LiteralPath $ConfigFile -PathType Leaf) {
    try {
        $config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($config.database_managed -eq $true -and $config.postgres_bin -and $config.postgres_data_dir) {
            $pgCtl = Join-Path ([string]$config.postgres_bin) 'pg_ctl.exe'
            if (Test-Path -LiteralPath $pgCtl -PathType Leaf) {
                & $pgCtl status -D ([string]$config.postgres_data_dir) *> $null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host 'Stopping the private LightHouse PostgreSQL cluster' -ForegroundColor Cyan
                    & $pgCtl stop -D ([string]$config.postgres_data_dir) -m fast -w *> $null
                }
            }
        }
    }
    catch {
        Write-Warning "Could not inspect or stop the private database cleanly: $($_.Exception.Message)"
    }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath) {
    $filtered = @($userPath -split ';' | Where-Object {
        $_ -and $_.TrimEnd('\') -ine $BinDir.TrimEnd('\')
    }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $filtered, 'User')
}

if ($KeepConfig) {
    Write-Host 'Removing application files while preserving config, encrypted secrets, instance records and private database data' -ForegroundColor Cyan
    foreach ($name in @('app', 'venv', 'bin', 'logs', 'start-server.ps1')) {
        Remove-Item -LiteralPath (Join-Path $InstallRoot $name) -Recurse -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host 'Removing LightHouse application files, managed instances, private database and user-bound encrypted secrets' -ForegroundColor Cyan
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
    $secretRoot = Join-Path $env:LOCALAPPDATA 'LightHouse\secrets'
    Remove-Item -LiteralPath $secretRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host 'LightHouse was removed. External PostgreSQL installations and databases were left untouched.' -ForegroundColor Green
