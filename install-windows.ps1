#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The public entry point stays tiny and PowerShell 5.1-safe. It prepares a
# private PostgreSQL control plane before running the validated 0.7 installer,
# so an existing postgres administrator password is never required.
$CoreCommit = 'f2ae0df9d69144218bcc68cb6538cae1755923fe'
$CoreUrl = "https://raw.githubusercontent.com/CPYMSU/LightHouse/$CoreCommit/install-windows.ps1"
$DatabaseHelperUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-database.ps1'

if ($env:LIGHTHOUSE_BOOTSTRAP_VALIDATE -eq '1') {
    Write-Output 'LightHouse Windows bootstrap OK'
    return
}

$coreFile = Join-Path $env:TEMP ("lighthouse-install-core-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
$databaseFile = Join-Path $env:TEMP ("lighthouse-install-database-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
$previousInstallFromFile = $env:LIGHTHOUSE_INSTALL_FROM_FILE

try {
    Invoke-WebRequest -UseBasicParsing -Uri $DatabaseHelperUrl -OutFile $databaseFile
    Invoke-WebRequest -UseBasicParsing -Uri $CoreUrl -OutFile $coreFile

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $databaseFile -Stage Prepare
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse private database preparation exited with code $LASTEXITCODE"
    }

    $env:LIGHTHOUSE_INSTALL_FROM_FILE = '1'
    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $coreFile
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse installer exited with code $LASTEXITCODE"
    }

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $databaseFile -Stage Finalize
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse private database finalization exited with code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $previousInstallFromFile) {
        Remove-Item Env:LIGHTHOUSE_INSTALL_FROM_FILE -ErrorAction SilentlyContinue
    }
    else {
        $env:LIGHTHOUSE_INSTALL_FROM_FILE = $previousInstallFromFile
    }
    Remove-Item -LiteralPath $coreFile, $databaseFile -Force -ErrorAction SilentlyContinue
}
