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
$PostgresPackageId = 'PostgreSQL.PostgreSQL.16'

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Write-WarningStep([string]$Message) {
    Write-Host $Message -ForegroundColor Yellow
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

function Get-ExternalDatabaseUrl([hashtable]$Config) {
    $environmentValue = [string]$env:LIGHTHOUSE_DATABASE_URL
    if ($environmentValue) {
        $environmentValue = $environmentValue.Trim()
        if ($environmentValue -notmatch '^postgres(?:ql)?://') {
            Fail 'LIGHTHOUSE_DATABASE_URL must be a postgresql:// or postgres:// URL'
        }
        return $environmentValue
    }

    $managed = $Config.ContainsKey('database_managed') -and [bool]$Config['database_managed']
    if (-not $managed -and $Config.ContainsKey('database_url')) {
        $configured = ([string]$Config['database_url']).Trim()
        if ($configured) { return $configured }
    }
    return ''
}

function Get-PostgresRuntimeReport([string]$Bin) {
    $missing = New-Object System.Collections.Generic.List[string]
    $root = if ($Bin) { Split-Path -Parent $Bin } else { '' }
    $version = ''
    $pkglibDir = ''
    $shareDir = ''

    if (-not $Bin -or -not (Test-Path -LiteralPath $Bin -PathType Container)) {
        return @{
            Valid = $false
            Bin = $Bin
            Root = $root
            Version = ''
            PkglibDir = ''
            ShareDir = ''
            Missing = @('PostgreSQL 16 bin directory')
        }
    }

    foreach ($name in @(
        'psql.exe',
        'initdb.exe',
        'pg_ctl.exe',
        'pg_isready.exe',
        'createdb.exe',
        'postgres.exe',
        'pg_config.exe'
    )) {
        $path = Join-Path $Bin $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $missing.Add($path)
        }
    }

    $psql = Join-Path $Bin 'psql.exe'
    if (Test-Path -LiteralPath $psql -PathType Leaf) {
        try { $version = (& $psql --version 2>$null | Out-String).Trim() } catch { $version = '' }
        if ($version -notmatch 'PostgreSQL\) 16\.') {
            $missing.Add("PostgreSQL 16 psql runtime (found: $version)")
        }
    }

    $pgConfig = Join-Path $Bin 'pg_config.exe'
    if (Test-Path -LiteralPath $pgConfig -PathType Leaf) {
        try { $pkglibDir = (& $pgConfig --pkglibdir 2>$null | Out-String).Trim() } catch { $pkglibDir = '' }
        try { $shareDir = (& $pgConfig --sharedir 2>$null | Out-String).Trim() } catch { $shareDir = '' }
    }
    if (-not $pkglibDir) { $pkglibDir = Join-Path $root 'lib' }
    if (-not $shareDir) { $shareDir = Join-Path $root 'share' }

    foreach ($path in @(
        (Join-Path $pkglibDir 'dict_snowball.dll'),
        (Join-Path $pkglibDir 'plpgsql.dll'),
        (Join-Path $shareDir 'postgres.bki'),
        (Join-Path $shareDir 'postgresql.conf.sample')
    )) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $missing.Add($path)
        }
    }

    return @{
        Valid = ($missing.Count -eq 0)
        Bin = $Bin
        Root = $root
        Version = $version
        PkglibDir = $pkglibDir
        ShareDir = $shareDir
        Missing = @($missing)
    }
}

function Test-Postgres16([string]$Bin) {
    return [bool](Get-PostgresRuntimeReport $Bin).Valid
}

function Get-PostgresCandidateBins {
    $candidates = New-Object System.Collections.Generic.List[string]
    $psql = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($psql -and $psql.Source) {
        $candidates.Add((Split-Path -Parent $psql.Source))
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles 'PostgreSQL\16\bin'))
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'PostgreSQL\16\bin'))
    }

    foreach ($registryRoot in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        try {
            foreach ($entry in @(Get-ItemProperty $registryRoot -ErrorAction SilentlyContinue)) {
                $displayName = [string]$entry.DisplayName
                $installLocation = [string]$entry.InstallLocation
                if ($displayName -match '^PostgreSQL 16' -and $installLocation) {
                    $candidates.Add((Join-Path $installLocation 'bin'))
                }
            }
        }
        catch { }
    }

    return @($candidates | Where-Object { $_ } | Select-Object -Unique)
}

function Resolve-PostgresBin {
    foreach ($bin in @(Get-PostgresCandidateBins)) {
        if (Test-Postgres16 $bin) { return $bin }
    }
    return $null
}

function Get-IncompletePostgresReport {
    foreach ($bin in @(Get-PostgresCandidateBins)) {
        $report = Get-PostgresRuntimeReport $bin
        if ($report.Version -match 'PostgreSQL\) 16\.' -and -not $report.Valid) {
            return $report
        }
    }
    return $null
}

function Wait-Postgres16([int]$Seconds = 300) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        $bin = Resolve-PostgresBin
        if ($bin) { return $bin }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Invoke-WingetPostgresInstall([bool]$Force) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Fail 'Windows Package Manager (winget) is required to install PostgreSQL 16'
    }

    try {
        & winget.exe source update --name winget --disable-interactivity *> $null
    }
    catch { }

    $installerPassword = [guid]::NewGuid().ToString('N')
    $override = "--mode unattended --unattendedmodeui none --superpassword `"$installerPassword`" --serverport 5432"
    $wingetArguments = @(
        'install',
        '--id', $PostgresPackageId,
        '--exact',
        '--source', 'winget',
        '--silent',
        '--accept-package-agreements',
        '--accept-source-agreements',
        '--disable-interactivity',
        '--override', $override
    )
    if ($Force) { $wingetArguments += '--force' }

    & winget.exe @wingetArguments
    return $LASTEXITCODE
}

function Repair-Postgres16 {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { return $false }

    Write-WarningStep 'The PostgreSQL 16 installation is incomplete; attempting an in-place repair'
    $repairHelp = ''
    try { $repairHelp = (& winget.exe repair --help 2>$null | Out-String) } catch { $repairHelp = '' }
    if ($repairHelp) {
        & winget.exe repair $PostgresPackageId --exact `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity --force
        if ($LASTEXITCODE -eq 0) {
            if (Wait-Postgres16 180) { return $true }
        }
    }

    Write-WarningStep 'WinGet repair was unavailable or incomplete; forcing the signed package installer to restore missing files'
    $exitCode = Invoke-WingetPostgresInstall $true
    if ($exitCode -ne 0) { return $false }
    return [bool](Wait-Postgres16 300)
}

function Ensure-Postgres16 {
    $bin = Resolve-PostgresBin
    if ($bin) { return $bin }

    $incomplete = Get-IncompletePostgresReport
    if ($incomplete) {
        if (Repair-Postgres16) {
            $bin = Resolve-PostgresBin
            if ($bin) { return $bin }
        }
        $details = (@($incomplete.Missing) -join '; ')
        Fail "PostgreSQL 16 is installed but incomplete. Missing runtime files: $details. Reinstall PostgreSQL.PostgreSQL.16 from the winget source and rerun the installer."
    }

    Write-Step 'Installing PostgreSQL 16 runtime for the private LightHouse Database Kernel'
    $exitCode = Invoke-WingetPostgresInstall $false
    if ($exitCode -ne 0) {
        Fail "winget failed to install $PostgresPackageId from the winget source (exit $exitCode)"
    }

    $bin = Wait-Postgres16 300
    if ($bin) { return $bin }

    $incomplete = Get-IncompletePostgresReport
    if ($incomplete -and (Repair-Postgres16)) {
        $bin = Resolve-PostgresBin
        if ($bin) { return $bin }
    }

    $missing = if ($incomplete) { @($incomplete.Missing) -join '; ' } else { 'runtime did not become discoverable' }
    Fail "PostgreSQL 16 installation did not complete correctly: $missing"
}

function Test-TcpListener([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $listener
}

function Test-PostgresReady([string]$PgIsReady, [int]$Port) {
    & $PgIsReady -h 127.0.0.1 -p $Port *> $null
    return $LASTEXITCODE -eq 0
}

function Find-PrivatePort {
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
    $externalDatabaseUrl = Get-ExternalDatabaseUrl $config
    if ($externalDatabaseUrl) {
        $config['database_url'] = $externalDatabaseUrl
        $config['database_managed'] = $false
        foreach ($key in @('database_port', 'postgres_bin', 'postgres_data_dir')) {
            if ($config.ContainsKey($key)) { $config.Remove($key) }
        }
        Save-Config $configFile $config
        Write-Step 'Using LIGHTHOUSE_DATABASE_URL or the existing explicitly configured PostgreSQL database'
        return
    }

    $managed = $config.ContainsKey('database_managed') -and [bool]$config['database_managed']
    $postgresBin = Ensure-Postgres16
    $runtime = Get-PostgresRuntimeReport $postgresBin
    if (-not $runtime.Valid) {
        Fail "PostgreSQL 16 runtime validation failed: $(@($runtime.Missing) -join '; ')"
    }

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

    if (Test-Path -LiteralPath $dataDir) {
        Remove-Item -LiteralPath $dataDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    $port = Find-PrivatePort
    $databasePassword = [guid]::NewGuid().ToString('N')
    $passwordFile = Join-Path $postgresRoot '.init-password'
    Write-Utf8NoBom $passwordFile ($databasePassword + "`n")
    Protect-CurrentUserFile $passwordFile

    Write-Step "Initializing the private LightHouse Database Kernel on 127.0.0.1:$port"
    $previousPath = $env:PATH
    $env:PATH = "$postgresBin;$previousPath"
    try {
        & $initdb -D $dataDir -U lighthouse "--pwfile=$passwordFile" `
            --auth-local=scram-sha-256 --auth-host=scram-sha-256 `
            --encoding=UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) {
            Remove-Item -LiteralPath $dataDir -Recurse -Force -ErrorAction SilentlyContinue
            $afterFailure = Get-PostgresRuntimeReport $postgresBin
            if (-not $afterFailure.Valid) {
                Fail "initdb detected an incomplete PostgreSQL runtime: $(@($afterFailure.Missing) -join '; ')"
            }
            Fail 'initdb failed for the private LightHouse database cluster; inspect the preceding PostgreSQL diagnostic'
        }
    }
    finally {
        $env:PATH = $previousPath
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
