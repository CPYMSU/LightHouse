#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Finalize', 'Validate')]
    [string]$Stage = 'Finalize'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$TaskName = 'LightHouse'
$ApiPort = 8787

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw "LightHouse background service installer: $Message"
}

function Get-InstallRoot {
    if ($env:LIGHTHOUSE_HOME) {
        return [IO.Path]::GetFullPath($env:LIGHTHOUSE_HOME)
    }
    return Join-Path $HOME '.lighthouse'
}

function Read-JsonConfig([string]$Path) {
    $result = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $result }
    try {
        $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $value.PSObject.Properties) {
            $result[$property.Name] = $property.Value
        }
    }
    catch {
        Fail "existing config.json is invalid: $($_.Exception.Message)"
    }
    return $result
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Protect-CurrentUserFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $Path /inheritance:r /grant:r "${identity}:(F)" | Out-Null
}

function Wait-ApiHealth([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok') { return $true }
        }
        catch { }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Show-StartupDiagnostics([string]$InstallRoot) {
    $logDir = Join-Path $InstallRoot 'logs'
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) { Write-Host "Scheduled Task state: $($task.State)" -ForegroundColor Yellow }
        if ($info) { Write-Host "Scheduled Task last result: $($info.LastTaskResult)" -ForegroundColor Yellow }
    }
    catch { }
    foreach ($name in @('startup-error.log', 'startup-direct-error.log', 'server.log', 'postgres.log')) {
        $path = Join-Path $logDir $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Write-Host "--- $name ---" -ForegroundColor Yellow
            Get-Content -LiteralPath $path -Tail 120 -ErrorAction SilentlyContinue
            Write-Host "--- end $name ---" -ForegroundColor Yellow
        }
    }
}

if ($Stage -eq 'Validate') {
    Write-Output 'LightHouse Windows background service installer OK'
    return
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Fail 'this installer supports Windows only'
}

$InstallRoot = Get-InstallRoot
$ConfigFile = Join-Path $InstallRoot 'config.json'
$Config = Read-JsonConfig $ConfigFile
$ManagedDatabase = $Config.ContainsKey('database_managed') -and [bool]$Config['database_managed']

$AppDir = Join-Path $InstallRoot 'app'
$VenvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'
$LhExe = Join-Path $InstallRoot 'venv\Scripts\lh.exe'
$LogDir = Join-Path $InstallRoot 'logs'
$ServerScript = Join-Path $InstallRoot 'start-server.ps1'
$ServerLog = Join-Path $LogDir 'server.log'
$StartupLog = Join-Path $LogDir 'startup-error.log'
$DirectError = Join-Path $LogDir 'startup-direct-error.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

foreach ($path in @($AppDir, $VenvPython, $LhExe, $ConfigFile)) {
    if (-not (Test-Path -LiteralPath $path)) { Fail "runtime path is missing: $path" }
}

$escapedApp = $AppDir.Replace("'", "''")
$escapedPython = $VenvPython.Replace("'", "''")
$escapedConfig = $ConfigFile.Replace("'", "''")
$escapedServerLog = $ServerLog.Replace("'", "''")
$escapedStartupLog = $StartupLog.Replace("'", "''")

$databasePrelude = ''
if ($ManagedDatabase) {
    $PostgresBin = [string]$Config['postgres_bin']
    $DataDir = [string]$Config['postgres_data_dir']
    $DatabasePort = [int]$Config['database_port']
    $PgCtl = Join-Path $PostgresBin 'pg_ctl.exe'
    $PostgresLog = Join-Path $LogDir 'postgres.log'
    foreach ($path in @($PgCtl, (Join-Path $DataDir 'PG_VERSION'))) {
        if (-not (Test-Path -LiteralPath $path)) { Fail "managed database runtime path is missing: $path" }
    }
    $escapedPgCtl = $PgCtl.Replace("'", "''")
    $escapedData = $DataDir.Replace("'", "''")
    $escapedPgLog = $PostgresLog.Replace("'", "''")
    $databasePrelude = @"
    & '$escapedPgCtl' status -D '$escapedData' *> `$null
    if (`$LASTEXITCODE -ne 0) {
        & '$escapedPgCtl' start -D '$escapedData' -l '$escapedPgLog' -o '-h 127.0.0.1 -p $DatabasePort' -w
        if (`$LASTEXITCODE -ne 0) { throw 'failed to start the private LightHouse PostgreSQL cluster' }
    }
"@
}

$serverContent = @"
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$env:LIGHTHOUSE_CONFIG = '$escapedConfig'
try {
    "`$(Get-Date -Format o) START" | Add-Content -LiteralPath '$escapedStartupLog' -Encoding UTF8
$databasePrelude
    Set-Location -LiteralPath '$escapedApp'
    "`$(Get-Date -Format o) STARTING API" | Add-Content -LiteralPath '$escapedStartupLog' -Encoding UTF8
    & '$escapedPython' -m lighthouse.server *>> '$escapedServerLog'
    `$exitCode = `$LASTEXITCODE
    "`$(Get-Date -Format o) API EXIT `$exitCode" | Add-Content -LiteralPath '$escapedStartupLog' -Encoding UTF8
    exit `$exitCode
}
catch {
    "`$(Get-Date -Format o) STARTUP ERROR`n`$(`$_ | Out-String)" | Add-Content -LiteralPath '$escapedStartupLog' -Encoding UTF8
    exit 1
}
"@
Write-Utf8NoBom $ServerScript $serverContent
Protect-CurrentUserFile $ServerScript

$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$TaskArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ServerScript`""
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $TaskArguments -WorkingDirectory $AppDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$DirectFallback = $false
if (-not (Wait-ApiHealth 20)) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -ne 'Running') {
        $DirectFallback = $true
        Write-Step 'Scheduled Task did not remain running; starting one direct background recovery process'
        Remove-Item -LiteralPath $DirectError -Force -ErrorAction SilentlyContinue
        Start-Process -FilePath $PowerShellExe -WindowStyle Hidden `
            -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $ServerScript) `
            -RedirectStandardError $DirectError | Out-Null
    }
}

if (-not (Wait-ApiHealth 45)) {
    Show-StartupDiagnostics $InstallRoot
    Fail 'LightHouse did not become healthy on port 8787 after Scheduled Task and direct-start checks'
}

& $LhExe migrate | Out-Null
if ($LASTEXITCODE -ne 0) {
    Show-StartupDiagnostics $InstallRoot
    Fail 'database migration failed after the API became healthy'
}
& $LhExe doctor
if ($LASTEXITCODE -ne 0) {
    Show-StartupDiagnostics $InstallRoot
    Fail 'LightHouse doctor reported an installation failure'
}

if ($DirectFallback) {
    Write-Step 'LightHouse recovered through a direct background start; the logon task remains registered for future sessions'
}
else {
    Write-Step 'LightHouse background task is healthy'
}
