#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The public entry point stays tiny and PowerShell 5.1-safe. Installation is
# deliberately sequenced as Database -> Application -> Background Service so
# no health check can run before the final startup script exists.
$DatabaseHelperUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-database.ps1?v=0.8.0'
$ApplicationCoreUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-core.ps1?v=0.8.0'
$ServiceInstallerUrl = 'https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows-service.ps1?v=0.8.0'

if ($env:LIGHTHOUSE_BOOTSTRAP_VALIDATE -eq '1') {
    Write-Output 'LightHouse Windows bootstrap OK'
    return
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

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $coreFile -Stage Install
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse application installation exited with code $LASTEXITCODE"
    }

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $serviceFile -Stage Finalize
    if ($LASTEXITCODE -ne 0) {
        throw "LightHouse background startup exited with code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $databaseFile, $coreFile, $serviceFile -Force -ErrorAction SilentlyContinue
}
