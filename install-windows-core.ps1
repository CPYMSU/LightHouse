#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Validate')]
    [string]$Stage = 'Install'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoUrl = 'https://github.com/CPYMSU/LightHouse.git'
$ControlService = 'com.cpym.su.lighthouse.control'
$ModelService = 'com.cpym.su.lighthouse.model'
$ApiPortStart = 8787

function Write-Step([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw "LightHouse application installer: $Message"
}

function Get-InstallRoot {
    if ($env:LIGHTHOUSE_HOME) {
        return [IO.Path]::GetFullPath($env:LIGHTHOUSE_HOME)
    }
    return Join-Path $HOME '.lighthouse'
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

function Save-Config([string]$Path, [hashtable]$Config) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Utf8NoBom $Path (($Config | ConvertTo-Json -Depth 20) + "`n")
    Protect-CurrentUserFile $Path
}

function Test-TcpListener([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $listener
}

function Find-FreeApiPort([int]$Start) {
    if ($Start -lt 1) { $Start = $ApiPortStart }
    for ($port = $Start; $port -le 65535; $port++) {
        if (-not (Test-TcpListener $port)) { return $port }
    }
    for ($port = 1024; $port -lt $Start; $port++) {
        if (-not (Test-TcpListener $port)) { return $port }
    }
    Fail 'no free local TCP port is available for the default LightHouse instance'
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
    if ($env:ProgramFiles) {
        foreach ($relative in @('Git\cmd\git.exe', 'Git\bin\git.exe')) {
            $candidate = Join-Path $env:ProgramFiles $relative
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    return $null
}

function Install-WingetPackage([string]$Id) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Fail 'Windows Package Manager (winget) is required. Install or update App Installer from Microsoft Store.'
    }
    & winget.exe install --id $Id --exact --silent `
        --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        Fail "winget failed to install $Id (exit $LASTEXITCODE)"
    }
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

if ($Stage -eq 'Validate') {
    Write-Output 'LightHouse Windows application installer OK'
    return
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
$LhCmd = Join-Path $BinDir 'lh.cmd'
New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir, $LogDir | Out-Null

try { Stop-ScheduledTask -TaskName 'LightHouse' -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Milliseconds 500

Write-Step 'Preparing the LightHouse Windows application runtime'
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
if (-not $Config.ContainsKey('database_url') -or -not [string]$Config['database_url']) {
    Fail 'database preparation did not provide database_url'
}
$PreferredApiPort = if ($Config.ContainsKey('port')) { [int]$Config['port'] } else { $ApiPortStart }
$ApiPort = Find-FreeApiPort $PreferredApiPort
if ($ApiPort -ne $PreferredApiPort) {
    Write-Step "Port $PreferredApiPort is occupied; assigning the default LightHouse instance to $ApiPort"
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
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($random) } finally { $generator.Dispose() }
    Set-LightHouseSecret $VenvPython $ControlService ([Convert]::ToBase64String($random))
}

$ModelBaseUrl = if ($env:LIGHTHOUSE_MODEL_BASE_URL) {
    $env:LIGHTHOUSE_MODEL_BASE_URL
}
elseif ($Config.ContainsKey('model_base_url')) {
    [string]$Config['model_base_url']
}
else { '' }

$ModelName = if ($env:LIGHTHOUSE_MODEL) {
    $env:LIGHTHOUSE_MODEL
}
elseif ($Config.ContainsKey('model')) {
    [string]$Config['model']
}
else { '' }

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
if (-not $ModelBaseUrl -or -not $ModelName) {
    Fail 'model API base URL and model name are required'
}

$Config['url'] = "http://127.0.0.1:$ApiPort"
$Config['model_base_url'] = $ModelBaseUrl.TrimEnd('/')
$Config['model'] = $ModelName
$Config['model_json_mode'] = $true
$Config['actor'] = $env:USERNAME
$Config['host'] = '127.0.0.1'
$Config['port'] = $ApiPort
$Config['platform'] = 'windows'
$Config['instance_id'] = 'default'
$Config['instance_name'] = 'default'
$Config['instance_kind'] = 'system'
Save-Config $ConfigFile $Config

$previousConfig = $env:LIGHTHOUSE_CONFIG
try {
    $env:LIGHTHOUSE_CONFIG = $ConfigFile
    & $VenvPython -c "from lighthouse.instances import ensure_default_instance; ensure_default_instance()"
    if ($LASTEXITCODE -ne 0) { Fail 'failed to register the default LightHouse instance' }
}
finally {
    if ($null -eq $previousConfig) {
        Remove-Item Env:LIGHTHOUSE_CONFIG -ErrorAction SilentlyContinue
    }
    else {
        $env:LIGHTHOUSE_CONFIG = $previousConfig
    }
}

$cmdContent = "@echo off`r`n`"$LhExe`" %*`r`n"
Write-Utf8NoBom $LhCmd $cmdContent

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathParts = @()
if ($userPath) { $pathParts = @($userPath -split ';' | Where-Object { $_ }) }
if (-not ($pathParts | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
    [Environment]::SetEnvironmentVariable('Path', (($pathParts + $BinDir) -join ';'), 'User')
}
if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
    $env:Path = "$env:Path;$BinDir"
}

Write-Step "LightHouse application runtime installed; default instance will start on port $ApiPort"
