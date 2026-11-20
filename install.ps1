[CmdletBinding()]
param(
    [string]$Python64 = 'python',
    [string]$Python32,
    [string]$InstallDir = (Join-Path $PSScriptRoot '.install'),
    [string]$MultisimPath = (Join-Path ${env:ProgramFiles(x86)} 'National Instruments\Circuit Design Suite 14.3')
)

$ErrorActionPreference = 'Stop'
# Keep our stage-specific messages when the parent PowerShell enables native-command errors.
$PSNativeCommandUseErrorActionPreference = $false

function Resolve-Python {
    param([string]$Command, [string]$Check)
    if (-not $Command) { return }
    $candidate = Get-Command $Command -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $candidate) { return }
    $path = & $candidate.Source -I -c "import struct, sys; sys.exit(1) if not ($Check) else print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $path) {
        return $path
    }
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

$check64 = 'struct.calcsize("P") == 8 and sys.version_info >= (3, 11)'
$check32 = 'struct.calcsize("P") == 4 and sys.version_info[:2] == (3, 12) and sys.implementation.name == "cpython"'
$launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Python32 -and $launcher) {
    $Python32 = & $launcher.Source -3.12-32 -I -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -ne 0) { $Python32 = $null }
}
$gatewayInterpreter = Resolve-Python $Python64 $check64
if (-not $gatewayInterpreter -and -not $PSBoundParameters.ContainsKey('Python64') -and $launcher) {
    $registered = & $launcher.Source -3.13-64 -I -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0) { $gatewayInterpreter = Resolve-Python $registered $check64 }
}
$backendInterpreter = Resolve-Python $Python32 $check32
if (-not $gatewayInterpreter) {
    throw 'The gateway requires 64-bit Python 3.11 or newer (tested with 3.13). Pass its python.exe with -Python64.'
}
if (-not $backendInterpreter) {
    throw 'The backends require 32-bit CPython 3.12. Install it or pass its python.exe with -Python32.'
}

$InstallDir = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

function Install-Environment {
    param([string]$Name, [string]$Interpreter, [string]$Check)
    $directory = Join-Path $InstallDir $Name
    $executable = Join-Path $directory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        Write-Host "Creating $Name"
        & $Interpreter -I -m venv $directory | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not create the $Name Python environment." }
    }
    if (-not (Resolve-Python $executable $Check)) {
        throw "The existing $Name environment has an invalid Python interpreter. Use a fresh -InstallDir."
    }
    Write-Host "Installing $Name dependencies"
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

$native = Install-Environment -Name 'native' -Interpreter $backendInterpreter -Check $check32
$com = Install-Environment -Name 'com' -Interpreter $backendInterpreter -Check $check32
$gatewayPython = Install-Environment -Name 'gateway' -Interpreter $gatewayInterpreter -Check $check64
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
} else {
    Write-Host "Preserving the existing gateway configuration. Verify its interpreter paths if you moved the install directory."
}
$clientText = @(
    '[mcp_servers.multisim_unified]'
    "command = $(ConvertTo-Json -Compress -InputObject $gateway)"
    "args = $(ConvertTo-Json -Compress -InputObject @('--config', $config))"
) -join [Environment]::NewLine
[IO.File]::WriteAllText($client, $clientText + [Environment]::NewLine, $utf8)

Write-Host "MCP client fragment: $client"
Write-Host "Gateway configuration: $config"
