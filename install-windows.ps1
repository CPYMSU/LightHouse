#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The public entry point stays tiny and PowerShell 5.1-safe. Installation is
# deliberately sequenced as Database -> Application -> Background Service so
# no health check can run before the final startup script exists.
$DatabaseHelperUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-database.ps1?v=1.8.0&rev=20260727-memory-resolution'
$ApplicationCoreUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-core.ps1?v=1.8.0&rev=20260727-memory-resolution'
$ServiceInstallerUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-service.ps1?v=1.8.0&rev=20260727-memory-resolution'

if ($env:LIGHTHOUSE_BOOTSTRAP_VALIDATE -eq '1') {
    Write-Output 'LightHouse Windows bootstrap OK'
    return
}

function Get-LightHouseInstallRoot {
    if ($env:LIGHTHOUSE_HOME) {
        return [IO.Path]::GetFullPath($env:LIGHTHOUSE_HOME)
    }
    return Join-Path $HOME '.lighthouse'
}

function Sync-LightHouseCommandPath {
    $installRoot = Get-LightHouseInstallRoot
    $binDir = Join-Path $installRoot 'bin'
    $lhCmd = Join-Path $binDir 'lh.cmd'
    if (-not (Test-Path -LiteralPath $lhCmd -PathType Leaf)) {
        throw "LightHouse command launcher was not created: $lhCmd"
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $userParts = @()
    if ($userPath) {
        $userParts = @($userPath -split ';' | Where-Object { $_ })
    }
    if (-not ($userParts | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') })) {
        [Environment]::SetEnvironmentVariable(
            'Path',
            (($userParts + $binDir) -join ';'),
            'User'
        )
    }

    $sessionParts = @($env:Path -split ';' | Where-Object { $_ })
    if (-not ($sessionParts | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') })) {
        $env:Path = (($sessionParts + $binDir) -join ';')
    }

    $resolved = Get-Command lh.cmd -ErrorAction SilentlyContinue
    if (-not $resolved) {
        throw "LightHouse was installed but lh.cmd is not discoverable in the current PowerShell session: $lhCmd"
    }
}

function Stop-LightHouseRuntime {
    $installRoot = Get-LightHouseInstallRoot
    $venvRoot = Join-Path $installRoot 'venv'

    try { Stop-ScheduledTask -TaskName 'LightHouse' -ErrorAction SilentlyContinue } catch { }
    try { & schtasks.exe /End /TN 'LightHouse' 2>$null | Out-Null } catch { }

    $currentPid = $PID
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    foreach ($process in $processes) {
        if ([int]$process.ProcessId -eq $currentPid) { continue }
        $executable = [string]$process.ExecutablePath
        $commandLine = [string]$process.CommandLine
        $fromVenv = $executable -and $executable.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase)
        $isLightHouse = $commandLine -and (
            $commandLine.IndexOf('lighthouse.server', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $commandLine.IndexOf('lighthouse.instance_entry', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $commandLine.IndexOf('LIGHTHOUSE_CONFIG', [StringComparison]::OrdinalIgnoreCase) -ge 0
        )
        if ($fromVenv -or $isLightHouse) {
            try { Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue } catch { }
        }
    }

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $locked = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $path = [string]$_.ExecutablePath
            $path -and $path.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase)
        })
        if ($locked.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'LightHouse runtime could not be stopped before upgrade. Close any open lh terminals and retry.'
}

$databaseFile = Join-Path $env:TEMP ("lighthouse-install-database-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
$coreFile = Join-Path $env:TEMP ("lighthouse-install-core-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
$serviceFile = Join-Path $env:TEMP ("lighthouse-install-service-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))

try {
    Invoke-WebRequest -UseBasicParsing -Uri $DatabaseHelperUrl -OutFile $databaseFile
    Invoke-WebRequest -UseBasicParsing -Uri $ApplicationCoreUrl -OutFile $coreFile
    Invoke-WebRequest -UseBasicParsing -Uri $ServiceInstallerUrl -OutFile $serviceFile

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $databaseFile -Stage Prepare
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse private database preparation exited with code $LASTEXITCODE"
    }

    Stop-LightHouseRuntime

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $coreFile -Stage Install
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse application installation exited with code $LASTEXITCODE"
    }
    Sync-LightHouseCommandPath

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $serviceFile -Stage Finalize
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse background startup exited with code $LASTEXITCODE"
    }
    Sync-LightHouseCommandPath
    Write-Host 'LightHouse is ready. Run: lh' -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $databaseFile, $coreFile, $serviceFile -Force -ErrorAction SilentlyContinue
}
