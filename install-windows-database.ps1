#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Finalize', 'Validate')]
    [string]$Stage = 'Prepare'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$TaskName = 'LightHouse'
$ApiPort = 8787
$DefaultPrivatePort = 55432

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw "LightHouse database bootstrap: $Message"
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

function Save-Config([string]$Path, [hashtable]$Config) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Utf8NoBom $Path (($Config | ConvertTo-Json -Depth 20) + "`n")
    Protect-CurrentUserFile $Path
}

function Test-Postgres16([string]$Bin) {
    $psql = Join-Path $Bin 'psql.exe'
    $initdb = Join-Path $Bin 'initdb.exe'
    $pgCtl = Join-Path $Bin 'pg_ctl.exe'
    if (-not (Test-Path -LiteralPath $psql -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $initdb -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $pgCtl -PathType Leaf)) { return $false }
    $version = (& $psql --version 2>$null | Out-String).Trim()
    return $version -match 'PostgreSQL\) 16\.'
}

function Resolve-PostgresBin {
    $psql = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($psql) {
        $bin = Split-Path -Parent $psql.Source
        if (Test-Postgres16 $bin) { return $bin }
    }
    if ($env:ProgramFiles) {
        $bin = Join-Path $env:ProgramFiles 'PostgreSQL\16\bin'
        if (Test-Postgres16 $bin) { return $bin }
    }
    return $null
}

function Install-Postgres16 {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Fail 'Windows Package Manager (winget) is required to install PostgreSQL 16'
    }
    Write-Step 'Installing PostgreSQL 16 runtime for the private LightHouse Database Kernel'
    $installerPassword = [guid]::NewGuid().ToString('N')
    $override = "--mode unattended --unattendedmodeui none --superpassword `"$installerPassword`" --serverport 5432"
    & winget.exe install --id PostgreSQL.PostgreSQL.16 --exact --silent `
        --accept-package-agreements --accept-source-agreements --disable-interactivity `
        --override $override
    if ($LASTEXITCODE -ne 0) {
        Fail "winget failed to install PostgreSQL.PostgreSQL.16 (exit $LASTEXITCODE)"
    }
}

function Test-TcpListener([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $listener
}

function Test-PostgresReady([string]$PgIsReady, [int]$Port) {
    & $PgIsReady -h 127.0.0.1 -p $Port *> $null
    return $LASTEXITCODE -eq 0
}

function Find-PrivatePort([string]$PgIsReady) {
    if (-not (Test-TcpListener 5432)) { return 5432 }
    if (-not (Test-PostgresReady $PgIsReady 5432)) {
        Fail 'port 5432 is occupied by a non-PostgreSQL process; free that port and rerun the installer'
    }
    for ($port = $DefaultPrivatePort; $port -lt ($DefaultPrivatePort + 100); $port++) {
        if (-not (Test-TcpListener $port)) { return $port }
    }
    Fail "no free private PostgreSQL port was found from $DefaultPrivatePort to $($DefaultPrivatePort + 99)"
}

function Start-PrivatePostgres(
    [string]$PostgresBin,
    [string]$DataDir,
    [string]$LogFile,
    [int]$Port
) {
    $pgCtl = Join-Path $PostgresBin 'pg_ctl.exe'
    $pgIsReady = Join-Path $PostgresBin 'pg_isready.exe'
    & $pgCtl status -D $DataDir *> $null
    if ($LASTEXITCODE -ne 0) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null
        & $pgCtl start -D $DataDir -l $LogFile -o "-h 127.0.0.1 -p $Port" -w
        if ($LASTEXITCODE -ne 0) { Fail 'failed to start the private LightHouse PostgreSQL cluster' }
    }
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-PostgresReady $pgIsReady $Port) { return }
        Start-Sleep -Seconds 1
    }
    Fail "private LightHouse PostgreSQL did not become ready on 127.0.0.1:$Port"
}

function Prepare-PrivateDatabase {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        Fail 'the private Windows database bootstrap supports Windows only'
    }

    $installRoot = Get-InstallRoot
    $configFile = Join-Path $installRoot 'config.json'
    $logDir = Join-Path $installRoot 'logs'
    $postgresRoot = Join-Path $installRoot 'postgres'
    $dataDir = Join-Path $postgresRoot 'data'
    $postgresLog = Join-Path $logDir 'postgres.log'
    New-Item -ItemType Directory -Force -Path $installRoot, $logDir, $postgresRoot | Out-Null

    $config = Read-JsonConfig $configFile
    $databaseUrl = if ($config.ContainsKey('database_url')) { [string]$config['database_url'] } else { '' }
    $managed = $config.ContainsKey('database_managed') -and [bool]$config['database_managed']

    if ($databaseUrl -and -not $managed) {
        Write-Step 'Using the existing explicitly configured LightHouse database'
        return
    }

    $postgresBin = Resolve-PostgresBin
    if (-not $postgresBin) {
        Install-Postgres16
        $postgresBin = Resolve-PostgresBin
    }
    if (-not $postgresBin) { Fail 'PostgreSQL 16 command-line tools did not become available' }

    $pgIsReady = Join-Path $postgresBin 'pg_isready.exe'
    $initdb = Join-Path $postgresBin 'initdb.exe'
    $psql = Join-Path $postgresBin 'psql.exe'
    $createdb = Join-Path $postgresBin 'createdb.exe'

    if ($managed) {
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'PG_VERSION') -PathType Leaf)) {
            Fail 'config.json declares a managed database but the private data directory is missing'
        }
        $port = if ($config.ContainsKey('database_port')) { [int]$config['database_port'] } else { $DefaultPrivatePort }
        Start-PrivatePostgres $postgresBin $dataDir $postgresLog $port
        Write-Step "Private LightHouse Database Kernel ready on 127.0.0.1:$port"
        return
    }

    if (Test-Path -LiteralPath (Join-Path $dataDir 'PG_VERSION') -PathType Leaf) {
        Fail 'a private PostgreSQL data directory exists without its LightHouse database configuration; restore config.json or remove the orphaned private data directory'
    }

    $port = Find-PrivatePort $pgIsReady
    $databasePassword = [guid]::NewGuid().ToString('N')
    $passwordFile = Join-Path $postgresRoot '.init-password'
    Write-Utf8NoBom $passwordFile ($databasePassword + "`n")
    Protect-CurrentUserFile $passwordFile

    Write-Step "Initializing the private LightHouse Database Kernel on 127.0.0.1:$port"
    try {
        & $initdb -D $dataDir -U lighthouse "--pwfile=$passwordFile" `
            --auth-local=scram-sha-256 --auth-host=scram-sha-256 --encoding=UTF8
        if ($LASTEXITCODE -ne 0) { Fail 'initdb failed for the private LightHouse database cluster' }
    }
    finally {
        Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue
    }

    Start-PrivatePostgres $postgresBin $dataDir $postgresLog $port

    $env:PGPASSWORD = $databasePassword
    try {
        $exists = (& $psql -h 127.0.0.1 -p $port -U lighthouse -d postgres -tAc `
            "SELECT 1 FROM pg_database WHERE datname='lighthouse'" | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { Fail 'failed to inspect the private LightHouse PostgreSQL cluster' }
        if ($exists -ne '1') {
            & $createdb -h 127.0.0.1 -p $port -U lighthouse -O lighthouse lighthouse
            if ($LASTEXITCODE -ne 0) { Fail 'failed to create the private lighthouse database' }
        }
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    $config['database_url'] = "postgresql://lighthouse:$databasePassword@127.0.0.1:$port/lighthouse"
    $config['database_managed'] = $true
    $config['database_port'] = $port
    $config['postgres_bin'] = $postgresBin
    $config['postgres_data_dir'] = $dataDir
    Save-Config $configFile $config

    Write-Step 'Private LightHouse Database Kernel initialized; no existing PostgreSQL password was required'
}

function Finalize-PrivateDatabaseService {
    $installRoot = Get-InstallRoot
    $configFile = Join-Path $installRoot 'config.json'
    $config = Read-JsonConfig $configFile
    if (-not ($config.ContainsKey('database_managed') -and [bool]$config['database_managed'])) { return }

    $postgresBin = [string]$config['postgres_bin']
    $dataDir = [string]$config['postgres_data_dir']
    $port = [int]$config['database_port']
    $appDir = Join-Path $installRoot 'app'
    $venvPython = Join-Path $installRoot 'venv\Scripts\python.exe'
    $logDir = Join-Path $installRoot 'logs'
    $serverScript = Join-Path $installRoot 'start-server.ps1'
    $pgCtl = Join-Path $postgresBin 'pg_ctl.exe'
    $postgresLog = Join-Path $logDir 'postgres.log'
    $serverLog = Join-Path $logDir 'server.log'

    foreach ($path in @($appDir, $venvPython, $pgCtl, (Join-Path $dataDir 'PG_VERSION'))) {
        if (-not (Test-Path -LiteralPath $path)) { Fail "managed runtime path is missing: $path" }
    }

    $escapedApp = $appDir.Replace("'", "''")
    $escapedPython = $venvPython.Replace("'", "''")
    $escapedPgCtl = $pgCtl.Replace("'", "''")
    $escapedData = $dataDir.Replace("'", "''")
    $escapedPgLog = $postgresLog.Replace("'", "''")
    $escapedServerLog = $serverLog.Replace("'", "''")

    $content = @"
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
& '$escapedPgCtl' status -D '$escapedData' *> `$null
if (`$LASTEXITCODE -ne 0) {
    & '$escapedPgCtl' start -D '$escapedData' -l '$escapedPgLog' -o '-h 127.0.0.1 -p $port' -w
    if (`$LASTEXITCODE -ne 0) { throw 'failed to start the private LightHouse PostgreSQL cluster' }
}
Set-Location -LiteralPath '$escapedApp'
& '$escapedPython' -m lighthouse.server *>> '$escapedServerLog'
"@
    Write-Utf8NoBom $serverScript $content
    Protect-CurrentUserFile $serverScript

    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
    Start-Sleep -Milliseconds 500
    Start-ScheduledTask -TaskName $TaskName

    $deadline = (Get-Date).AddSeconds(60)
    do {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/healthz" -TimeoutSec 2
            if ($health.status -eq 'ok') {
                Write-Step 'LightHouse background task now owns its private Database Kernel'
                return
            }
        }
        catch { }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    Fail 'LightHouse did not become healthy after enabling the private database startup sequence'
}

if ($Stage -eq 'Validate') {
    Write-Output 'LightHouse private database bootstrap OK'
    return
}
if ($Stage -eq 'Prepare') {
    Prepare-PrivateDatabase
    return
}
Finalize-PrivateDatabaseService
