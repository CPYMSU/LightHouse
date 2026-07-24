#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoUrl = 'https://github.com/CPYMSU/LightHouse.git'
$RawInstallUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1'
$TaskName = 'LightHouse'
$ControlService = 'com.cpym.su.lighthouse.control'
$ModelService = 'com.cpym.su.lighthouse.model'
$Port = 8787

# `irm ... | iex` executes from a pipeline. Re-exec from a complete temporary
# file before launching winget or installers so child processes cannot consume
# the remaining source and so the script has a stable path for diagnostics.
if (-not $MyInvocation.MyCommand.Path -and $env:LIGHTHOUSE_INSTALL_FROM_FILE -ne '1') {
    $bootstrap = Join-Path $env:TEMP ("lighthouse-install-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
    Invoke-WebRequest -UseBasicParsing -Uri $RawInstallUrl -OutFile $bootstrap
    $env:LIGHTHOUSE_INSTALL_FROM_FILE = '1'
    try {
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $bootstrap
        exit $LASTEXITCODE
    }
    finally {
        Remove-Item -LiteralPath $bootstrap -Force -ErrorAction SilentlyContinue
    }
}

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw "LightHouse installer: $Message"
}

function ConvertTo-PlainText([Security.SecureString]$Secure) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Get-InstallRoot {
    if ($env:LIGHTHOUSE_HOME) {
        return [IO.Path]::GetFullPath($env:LIGHTHOUSE_HOME)
    }
    return Join-Path $HOME '.lighthouse'
}

function Test-Python312([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    & $Path -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Resolve-Python312 {
    $candidates = New-Object System.Collections.Generic.List[string]
    $commands = @(Get-Command -Name @('python3.12.exe', 'python.exe') -ErrorAction SilentlyContinue)
    foreach ($command in $commands) {
        if ($command -and $command.Source) { $candidates.Add($command.Source) }
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'Python312\python.exe'))
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'Python312\python.exe'))
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Python312 $candidate) { return $candidate }
    }
    return $null
}

function Resolve-Git {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
        (Join-Path $env:ProgramFiles 'Git\bin\git.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Test-Postgres16([string]$Bin) {
    $psql = Join-Path $Bin 'psql.exe'
    if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) { return $false }
    $version = (& $psql --version 2>$null | Out-String).Trim()
    return $version -match 'PostgreSQL\) 16\.'
}

function Resolve-PostgresBin {
    $psql = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($psql) {
        $bin = Split-Path -Parent $psql.Source
        if (Test-Postgres16 $bin) { return $bin }
    }
    $bin = Join-Path $env:ProgramFiles 'PostgreSQL\16\bin'
    if (Test-Postgres16 $bin) { return $bin }
    return $null
}

function Install-WingetPackage([string]$Id, [string]$Override = '') {
    $arguments = @(
        'install', '--id', $Id, '--exact', '--silent',
        '--accept-package-agreements', '--accept-source-agreements',
        '--disable-interactivity'
    )
    if ($Override) {
        $arguments += @('--override', $Override)
    }
    & winget.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "winget failed to install $Id (exit $LASTEXITCODE)"
    }
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

function Test-LightHouseSecret([string]$Python, [string]$Kind) {
    $script = if ($Kind -eq 'control') {
        "from lighthouse.secrets import control_api_key; print('1' if len(control_api_key()) >= 16 else '0')"
    }
    else {
        "from lighthouse.secrets import model_api_key; print('1' if bool(model_api_key()) else '0')"
    }
    $value = (& $Python -c $script 2>$null | Out-String).Trim()
    return $value -eq '1'
}

function Set-LightHouseSecret([string]$Python, [string]$Service, [string]$Value) {
    $env:LIGHTHOUSE_INSTALL_SECRET_VALUE = $Value
    $env:LIGHTHOUSE_INSTALL_SECRET_SERVICE = $Service
    try {
        & $Python -c "import os; from lighthouse.secrets import keychain_set; keychain_set(os.environ['LIGHTHOUSE_INSTALL_SECRET_SERVICE'], os.environ['LIGHTHOUSE_INSTALL_SECRET_VALUE'])"
        if ($LASTEXITCODE -ne 0) { Fail "failed to store $Service in Windows DPAPI" }
    }
    finally {
        Remove-Item Env:LIGHTHOUSE_INSTALL_SECRET_VALUE -ErrorAction SilentlyContinue
        Remove-Item Env:LIGHTHOUSE_INSTALL_SECRET_SERVICE -ErrorAction SilentlyContinue
    }
}

function Wait-HttpHealth([int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok') { return $true }
        }
        catch { }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    Fail 'this installer supports Windows only'
}

$InstallRoot = Get-InstallRoot
$AppDir = Join-Path $InstallRoot 'app'
$VenvDir = Join-Path $InstallRoot 'venv'
$BinDir = Join-Path $InstallRoot 'bin'
$LogDir = Join-Path $InstallRoot 'logs'
$ConfigFile = Join-Path $InstallRoot 'config.json'
$ServerScript = Join-Path $InstallRoot 'start-server.ps1'
$LhCmd = Join-Path $BinDir 'lh.cmd'

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir, $LogDir | Out-Null

# Stop an older per-user service before replacing its environment. A remaining
# listener means another application owns the control-plane port, so fail closed.
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Milliseconds 500
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) { Fail "port $Port is already in use by PID $($listener.OwningProcess)" }

Write-Step 'Installing LightHouse OS for Windows — PowerShell, PostgreSQL and native Desktop Kernel'

if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    Fail 'Windows Package Manager (winget) is required. Install or update App Installer from Microsoft Store.'
}

$Python = Resolve-Python312
if (-not $Python) {
    Write-Step 'Installing Python 3.12'
    Install-WingetPackage 'Python.Python.3.12'
    $Python = Resolve-Python312
}
if (-not $Python) { Fail 'Python 3.12 did not become available' }

$Git = Resolve-Git
if (-not $Git) {
    Write-Step 'Installing Git'
    Install-WingetPackage 'Git.Git'
    $Git = Resolve-Git
}
if (-not $Git) { Fail 'Git did not become available' }

$Config = Read-JsonConfig $ConfigFile
$DatabaseUrl = if ($Config.ContainsKey('database_url')) { [string]$Config['database_url'] } else { '' }
$PostgresBin = Resolve-PostgresBin
$PostgresPassword = $env:LIGHTHOUSE_POSTGRES_PASSWORD
if (-not $PostgresBin) {
    Write-Step 'Installing PostgreSQL 16'
    if (-not $PostgresPassword) { $PostgresPassword = [guid]::NewGuid().ToString('N') }
    $override = "--mode unattended --unattendedmodeui none --superpassword `"$PostgresPassword`" --serverport 5432"
    Install-WingetPackage 'PostgreSQL.PostgreSQL.16' $override
    $PostgresBin = Resolve-PostgresBin
}
if (-not $PostgresBin) { Fail 'PostgreSQL 16 did not become available' }

$PgIsReady = Join-Path $PostgresBin 'pg_isready.exe'
$Psql = Join-Path $PostgresBin 'psql.exe'
$Createdb = Join-Path $PostgresBin 'createdb.exe'
$postgresService = Get-Service -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -like 'postgresql*16*' -or $_.DisplayName -like '*PostgreSQL*16*'
} | Select-Object -First 1
if ($postgresService -and $postgresService.Status -ne 'Running') {
    try { Start-Service -Name $postgresService.Name -ErrorAction Stop } catch { }
}
for ($i = 0; $i -lt 60; $i++) {
    & $PgIsReady -h 127.0.0.1 -p 5432 *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
& $PgIsReady -h 127.0.0.1 -p 5432 *> $null
if ($LASTEXITCODE -ne 0) { Fail 'PostgreSQL did not become ready on 127.0.0.1:5432' }

if (-not $DatabaseUrl) {
    if (-not $PostgresPassword) {
        $secure = Read-Host 'Existing PostgreSQL postgres-user password' -AsSecureString
        $PostgresPassword = ConvertTo-PlainText $secure
    }
    if (-not $PostgresPassword) { Fail 'PostgreSQL administrator password is required' }
    $DatabaseRolePassword = [guid]::NewGuid().ToString('N')
    $env:PGPASSWORD = $PostgresPassword
    try {
        $auth = (& $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc 'SELECT 1' 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $auth -ne '1') {
            Fail 'could not authenticate to PostgreSQL as postgres; set LIGHTHOUSE_POSTGRES_PASSWORD and rerun'
        }
        $roleExists = (& $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='lighthouse'" | Out-String).Trim()
        if ($roleExists -eq '1') {
            & $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE lighthouse WITH LOGIN PASSWORD '$DatabaseRolePassword'" | Out-Null
        }
        else {
            & $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE lighthouse LOGIN PASSWORD '$DatabaseRolePassword'" | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { Fail 'failed to create the LightHouse PostgreSQL role' }
        $exists = (& $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='lighthouse'" | Out-String).Trim()
        if ($exists -ne '1') {
            & $Createdb -h 127.0.0.1 -p 5432 -U postgres -O lighthouse lighthouse
            if ($LASTEXITCODE -ne 0) { Fail 'failed to create the lighthouse database' }
        }
        & $Psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'ALTER DATABASE lighthouse OWNER TO lighthouse' | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail 'failed to assign the lighthouse database owner' }
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    $DatabaseUrl = "postgresql://lighthouse:$DatabaseRolePassword@127.0.0.1:5432/lighthouse"
}

Write-Step 'Downloading LightHouse'
if (Test-Path -LiteralPath (Join-Path $AppDir '.git') -PathType Container) {
    & $Git -C $AppDir fetch --prune origin main
    if ($LASTEXITCODE -ne 0) { Fail 'git fetch failed' }
    & $Git -C $AppDir reset --hard origin/main
    if ($LASTEXITCODE -ne 0) { Fail 'git reset failed' }
}
else {
    Remove-Item -LiteralPath $AppDir -Recurse -Force -ErrorAction SilentlyContinue
    & $Git clone --depth 1 --branch main $RepoUrl $AppDir
    if ($LASTEXITCODE -ne 0) { Fail 'git clone failed' }
}

Write-Step 'Installing the complete LightHouse package'
& $Python -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { Fail 'failed to create the Python virtual environment' }
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPip = Join-Path $VenvDir 'Scripts\pip.exe'
$LhExe = Join-Path $VenvDir 'Scripts\lh.exe'
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail 'pip upgrade failed' }
& $VenvPip install --upgrade $AppDir
if ($LASTEXITCODE -ne 0) { Fail 'LightHouse package installation failed' }

if (-not (Test-LightHouseSecret $VenvPython 'control')) {
    $random = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($random)
    $ControlKey = [Convert]::ToBase64String($random)
    Set-LightHouseSecret $VenvPython $ControlService $ControlKey
}

$ModelBaseUrl = if ($env:LIGHTHOUSE_MODEL_BASE_URL) { $env:LIGHTHOUSE_MODEL_BASE_URL } elseif ($Config.ContainsKey('model_base_url')) { [string]$Config['model_base_url'] } else { '' }
$ModelName = if ($env:LIGHTHOUSE_MODEL) { $env:LIGHTHOUSE_MODEL } elseif ($Config.ContainsKey('model')) { [string]$Config['model'] } else { '' }
$HasModelKey = Test-LightHouseSecret $VenvPython 'model'
$ModelKey = $env:LIGHTHOUSE_MODEL_API_KEY
if (-not $ModelBaseUrl) { $ModelBaseUrl = Read-Host 'Model API base URL (for example https://api.openai.com/v1)' }
if (-not $ModelName) { $ModelName = Read-Host 'Model name' }
if (-not $HasModelKey) {
    if (-not $ModelKey) {
        $secureModel = Read-Host 'Model API key' -AsSecureString
        $ModelKey = ConvertTo-PlainText $secureModel
    }
    if (-not $ModelKey) { Fail 'model API key is required' }
    Set-LightHouseSecret $VenvPython $ModelService $ModelKey
}
if (-not $ModelBaseUrl -or -not $ModelName) { Fail 'model API base URL and model name are required' }

$Config['url'] = "http://127.0.0.1:$Port"
$Config['database_url'] = $DatabaseUrl
$Config['model_base_url'] = $ModelBaseUrl.TrimEnd('/')
$Config['model'] = $ModelName
$Config['model_json_mode'] = $true
$Config['actor'] = $env:USERNAME
$Config['host'] = '127.0.0.1'
$Config['port'] = $Port
$Config['platform'] = 'windows'
Write-Utf8NoBom $ConfigFile (($Config | ConvertTo-Json -Depth 20) + "`n")
Protect-CurrentUserFile $ConfigFile

$escapedApp = $AppDir.Replace("'", "''")
$escapedPython = $VenvPython.Replace("'", "''")
$escapedLog = (Join-Path $LogDir 'server.log').Replace("'", "''")
$serverContent = @"
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath '$escapedApp'
& '$escapedPython' -m lighthouse.server *>> '$escapedLog'
"@
Write-Utf8NoBom $ServerScript $serverContent
Protect-CurrentUserFile $ServerScript

$cmdContent = "@echo off`r`n`"$LhExe`" %*`r`n"
Write-Utf8NoBom $LhCmd $cmdContent

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathParts = @()
if ($userPath) { $pathParts = @($userPath -split ';' | Where-Object { $_ }) }
if (-not ($pathParts | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
    $newUserPath = (($pathParts + $BinDir) -join ';')
    [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
}
if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
    $env:Path = "$env:Path;$BinDir"
}

Write-Step 'Installing the LightHouse background task'
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
$powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
if (-not $powerShellCommand) { Fail 'Windows PowerShell 5.1 is required to host the background service' }
$powerShellExe = $powerShellCommand.Source
$taskArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ServerScript`""
$action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $taskArguments -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Step 'Waiting for LightHouse'
if (-not (Wait-HttpHealth 60)) {
    $log = Join-Path $LogDir 'server.log'
    if (Test-Path -LiteralPath $log) {
        Write-Host '--- LightHouse server log ---' -ForegroundColor Yellow
        Get-Content -LiteralPath $log -Tail 100
        Write-Host '--- end server log ---' -ForegroundColor Yellow
    }
    Fail "LightHouse did not become healthy on port $Port"
}

& $LhExe migrate | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'database migration failed' }
& $LhExe doctor
if ($LASTEXITCODE -ne 0) { Fail 'LightHouse doctor reported an installation failure' }

Write-Step 'LightHouse is installed.'
Write-Host ''
Write-Host 'Open Windows Terminal or PowerShell, enter a project, and run:'
Write-Host ''
Write-Host '  cd C:\path\to\project'
Write-Host '  lh'
Write-Host ''
Write-Host 'If this existing window does not see lh, open a new Windows Terminal tab.'
