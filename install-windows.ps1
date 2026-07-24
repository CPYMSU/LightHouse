#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The public installer is intentionally a tiny PowerShell 5.1-safe bootstrap.
# Invoke-Expression does not guarantee that $MyInvocation.MyCommand exposes a
# Path property under StrictMode, so this entry point never reads that property.
$CoreCommit = 'f2ae0df9d69144218bcc68cb6538cae1755923fe'
$CoreUrl = "https://raw.githubusercontent.com/CPYMSU/LightHouse/$CoreCommit/install-windows.ps1"

if ($env:LIGHTHOUSE_BOOTSTRAP_VALIDATE -eq '1') {
    Write-Output 'LightHouse Windows bootstrap OK'
    return
}

$bootstrap = Join-Path $env:TEMP ("lighthouse-install-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))
$previousInstallFromFile = $env:LIGHTHOUSE_INSTALL_FROM_FILE

try {
    Invoke-WebRequest -UseBasicParsing -Uri $CoreUrl -OutFile $bootstrap
    $env:LIGHTHOUSE_INSTALL_FROM_FILE = '1'

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $bootstrap
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "LightHouse installer exited with code $exitCode"
    }
}
finally {
    if ($null -eq $previousInstallFromFile) {
        Remove-Item Env:LIGHTHOUSE_INSTALL_FROM_FILE -ErrorAction SilentlyContinue
    }
    else {
        $env:LIGHTHOUSE_INSTALL_FROM_FILE = $previousInstallFromFile
    }
    Remove-Item -LiteralPath $bootstrap -Force -ErrorAction SilentlyContinue
}
