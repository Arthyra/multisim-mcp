[CmdletBinding()]
param(
    [string]$Python64 = 'python',
    [string]$Python32,
    [string]$InstallDir = (Join-Path $PSScriptRoot '.install'),
    [string]$MultisimPath = (Join-Path ${env:ProgramFiles(x86)} 'National Instruments\Circuit Design Suite 14.3')
)

$ErrorActionPreference = 'Stop'

if (-not $Python32) {
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $launcher) {
        throw 'Install 32-bit CPython 3.12 and supply its python.exe with -Python32.'
    }
    $Python32 = & $launcher.Source -3.12-32 -c 'import sys; print(sys.executable)'
    if ($LASTEXITCODE -ne 0 -or -not $Python32) {
        throw 'The Python launcher could not find 32-bit CPython 3.12. Pass -Python32 explicitly.'
    }
}

& $Python64 -c 'import struct, sys; sys.exit(not (struct.calcsize("P") == 8 and sys.version_info >= (3, 11)))'
if ($LASTEXITCODE -ne 0) {
    throw 'The gateway requires 64-bit Python 3.11 or newer. Pass its python.exe with -Python64.'
}
& $Python32 -c 'import struct, sys; sys.exit(not (struct.calcsize("P") == 4 and sys.version_info[:2] == (3, 12) and sys.implementation.name == "cpython"))'
if ($LASTEXITCODE -ne 0) {
    throw 'The backends require 32-bit CPython 3.12. Pass its python.exe with -Python32.'
}

$MultisimPath = [IO.Path]::GetFullPath($MultisimPath)
$multisimExe = Join-Path $MultisimPath 'multisim.exe'
if (-not (Test-Path -LiteralPath $multisimExe -PathType Leaf)) {
    throw 'Multisim was not found. Install and activate it, or pass its program directory with -MultisimPath.'
}
$build = (Get-Item -LiteralPath $multisimExe).VersionInfo.FileVersion
if ($build -ne '14.3.1500.0') {
    throw "Detected Multisim build $build. This installer supports the tested build 14.3.1500.0."
}

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

function Install-Environment {
    param([string]$Name, [string]$Interpreter)
    $directory = Join-Path $InstallDir $Name
    Write-Host "Installing $Name"
    & $Interpreter -m venv $directory | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not create the $Name Python environment." }
    $executable = Join-Path $directory 'Scripts\python.exe'
    $lock = Join-Path $PSScriptRoot "requirements\$Name.txt"
    & $executable -m pip --disable-pip-version-check --timeout 45 install --no-deps --only-binary=:all: -r $lock | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not install $Name. See the pip error above." }
    if ($Name -eq 'gateway') {
        & $executable -m pip --disable-pip-version-check --timeout 45 install --no-deps $PSScriptRoot | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the gateway package.' }
    }
    & $executable -m pip --disable-pip-version-check check | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "The $Name environment has incompatible dependencies." }
    return $executable
}

$native = Install-Environment -Name 'native' -Interpreter $Python32
$com = Install-Environment -Name 'com' -Interpreter $Python32
$gatewayPython = Install-Environment -Name 'gateway' -Interpreter $Python64
$gateway = Join-Path (Split-Path -Parent $gatewayPython) 'multisim-unified-mcp.exe'

$config = Join-Path $InstallDir 'multisim.local.toml'
$client = Join-Path $InstallDir 'multisim.client.toml'
$utf8 = [Text.UTF8Encoding]::new($false)
if (-not (Test-Path -LiteralPath $config)) {
    $configText = @(
        '[native]'
        "command = $(ConvertTo-Json -Compress -InputObject $native)"
        ''
        '[native.env]'
        "MULTISIM_NATIVE_INSTALL = $(ConvertTo-Json -Compress -InputObject $MultisimPath)"
        ''
        '[com]'
        "command = $(ConvertTo-Json -Compress -InputObject $com)"
        'args = ["-m", "multisim_mcp.server"]'
        ''
        '[com.env]'
        "MULTISIM_MCP_WORKER_PYTHON = $(ConvertTo-Json -Compress -InputObject $com)"
    ) -join [Environment]::NewLine
    [IO.File]::WriteAllText($config, $configText + [Environment]::NewLine, $utf8)
}
$clientText = @(
    '[mcp_servers.multisim_unified]'
    "command = $(ConvertTo-Json -Compress -InputObject $gateway)"
    "args = $(ConvertTo-Json -Compress -InputObject @('--config', $config))"
) -join [Environment]::NewLine
[IO.File]::WriteAllText($client, $clientText + [Environment]::NewLine, $utf8)

Write-Host "MCP client fragment: $client"
Write-Host "Gateway configuration: $config"
